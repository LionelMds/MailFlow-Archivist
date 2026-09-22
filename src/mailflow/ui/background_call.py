"""Keep Qt responsive while waiting for network-only work.

Outlook COM objects and database operations must stay on their owning thread.
Only the detached mail metadata and the OpenAI request cross this boundary.
Callers disable mutating controls while the local event loop is running.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from PySide6.QtCore import QCoreApplication, QEventLoop, QThread

from mailflow.classifier.pipeline import AiClassifierProtocol
from mailflow.models import AiMailClassification, MailMetadata

T = TypeVar("T")


class _CallThread(QThread):
    def __init__(self, callback: Callable[[], Any]) -> None:
        super().__init__()
        self.callback = callback
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self.callback()
        except BaseException as exc:
            self.error = exc


def run_with_event_loop(callback: Callable[[], T]) -> T:
    """Return a network result synchronously, servicing Qt events while waiting."""
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        return callback()
    loop = QEventLoop()
    worker = _CallThread(callback)
    worker.finished.connect(loop.quit)
    worker.start()
    try:
        loop.exec()
    finally:
        # Never leave a running QThread behind, including when Qt is shutting down.
        worker.wait()
    error = worker.error
    result = worker.result
    worker.deleteLater()
    if error is not None:
        raise error
    return cast(T, result)


class ResponsiveAiClassifier:
    """Adapt the existing classifier without moving Outlook or SQLite to Qt workers."""

    def __init__(self, classifier: AiClassifierProtocol) -> None:
        self.classifier = classifier

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        return run_with_event_loop(
            lambda: self.classifier.classify(
                mail,
                include_body=include_body,
                privacy_mask_phone_numbers=privacy_mask_phone_numbers,
                known_context=known_context,
            )
        )
