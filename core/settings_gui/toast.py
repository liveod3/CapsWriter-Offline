"""Transient feedback inside a Qt window, independent of dictation overlays."""

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QLabel


class GuiToast(QLabel):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName('guiToast')
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMargin(14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet('QLabel#guiToast { background: #303449; color: white; '
                           'border: 1px solid #454a63; border-radius: 10px; font-size: 13px; }')
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(2400)
        self.timer.timeout.connect(self.dismiss)
        window.installEventFilter(self)
        self.hide()

    def present(self, message):
        if not self.parentWidget().isVisible():
            return
        self.setText(message)
        self.reposition()
        self.show()
        self.raise_()
        self.timer.start()

    def reposition(self):
        window = self.parentWidget()
        width = min(max(1, window.width() - 40), max(240, self.fontMetrics().horizontalAdvance(self.text()) + 40))
        self.setFixedSize(width, max(46, self.heightForWidth(width)))
        self.move((window.width() - self.width()) // 2,
                  max(0, min(window.height() - self.height() - 20,
                             round(window.height() * 0.8 - self.height() / 2))))

    def dismiss(self):
        self.timer.stop()
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self.reposition()
        elif event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self.dismiss()
        return super().eventFilter(watched, event)
