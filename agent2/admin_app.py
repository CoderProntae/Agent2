"""Standalone UsageLimitEditor window sharing the main app's local SQLite policy."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .core.usage import UsageLimits, UsageStore

ADMIN_STYLE = """
QMainWindow, QWidget { background:#101319; color:#e6eaf0; font-family:'Segoe UI',sans-serif; font-size:13px; }
QFrame#card { background:#151a22; border:1px solid #2a3340; border-radius:10px; }
QLabel#title { font-size:21px; font-weight:700; }
QLabel#muted { color:#96a1b0; }
QLineEdit,QSpinBox { background:#0d1117; border:1px solid #303a48; border-radius:6px; padding:7px; color:#e6eaf0; }
QPushButton { background:#202735; border:1px solid #384456; border-radius:6px; padding:8px 12px; }
QPushButton:hover { background:#2a3545; }
QPushButton#primary { background:#3566d6; border-color:#4778e6; color:white; font-weight:700; }
QCheckBox { spacing:8px; }
"""


class PinGateDialog(QDialog):
    def __init__(self, store: UsageStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Yönetici doğrulaması · UsageLimitEditor")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        title = QLabel("Kullanım sınırı yöneticisi")
        title.setObjectName("title")
        layout.addWidget(title)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setObjectName("muted")
        layout.addWidget(self.message)
        form = QFormLayout()
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setPlaceholderText("En az 8 karakter")
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setPlaceholderText("Parolayı yeniden yazın")
        form.addRow("Yönetici parolası", self.pin)
        form.addRow("Parolayı doğrula", self.confirm)
        layout.addLayout(form)
        self.confirm_row = True
        if store.has_admin_pin():
            self.message.setText("Devam etmek için yönetici parolanızı doğrulayın.")
            self.confirm.hide()
            form.labelForField(self.confirm).hide()
            self.confirm_row = False
        else:
            self.message.setText("İlk kullanım: yerel kota ayarlarını korumak için yönetici parolası belirleyin. Bu parola işletim sistemi anahtarlığına değil, yerel veritabanında PBKDF2 özeti olarak kaydedilir.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Devam et")
        buttons.accepted.connect(self._verify)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.pin.returnPressed.connect(self._verify)

    def _verify(self) -> None:
        pin = self.pin.text()
        if self.store.has_admin_pin():
            if not self.store.verify_admin_pin(pin):
                QMessageBox.warning(self, "Doğrulama başarısız", "Yönetici parolası yanlış.")
                self.pin.clear()
                return
        else:
            if len(pin) < 8:
                QMessageBox.warning(self, "Parola çok kısa", "En az 8 karakter kullanın.")
                return
            if pin != self.confirm.text():
                QMessageBox.warning(self, "Parolalar eşleşmiyor", "İki parola aynı olmalı.")
                self.confirm.clear()
                return
            try:
                self.store.set_admin_pin(pin)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Parola kaydedilemedi", str(exc))
                return
        self.accept()


class UsageEditorWindow(QMainWindow):
    def __init__(self, store: UsageStore) -> None:
        super().__init__()
        self.store = store
        self.setWindowTitle("UsageLimitEditor · Agent2 Yönetici Aracı")
        self.resize(700, 720)
        self.setMinimumSize(600, 620)
        container = QWidget()
        self.setCentralWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(20, 20, 20, 18)
        root.setSpacing(14)

        title = QLabel("Kullanım ve kota yönetimi")
        title.setObjectName("title")
        root.addWidget(title)
        subtitle = QLabel("Ayarlar ana Agent2 uygulaması tarafından canlı okunur. Kilit, kota aşımlarından bağımsız olarak eylemleri durdurur.")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)
        stats_title = QLabel("BUGÜNKÜ KULLANIM")
        stats_title.setStyleSheet("font-weight:700; letter-spacing:1px;")
        card_layout.addWidget(stats_title)
        self.stats_label = QLabel()
        self.stats_label.setObjectName("muted")
        self.stats_label.setWordWrap(True)
        card_layout.addWidget(self.stats_label)
        root.addWidget(card)

        limits_card = QFrame()
        limits_card.setObjectName("card")
        limits_layout = QVBoxLayout(limits_card)
        limits_layout.setContentsMargins(16, 14, 16, 14)
        limits_layout.setSpacing(9)
        limits_heading = QLabel("KOTA EŞİKLERİ")
        limits_heading.setStyleSheet("font-weight:700; letter-spacing:1px;")
        limits_layout.addWidget(limits_heading)
        form = QFormLayout()
        self.spins: dict[str, QSpinBox] = {}
        labels = [
            ("max_daily_requests", "Günlük Ollama isteği", 1, 100_000),
            ("max_daily_tokens", "Günlük toplam token", 1, 100_000_000),
            ("max_daily_executions", "Günlük komut yürütme", 1, 100_000),
            ("max_session_tokens", "Oturum başına token", 1, 10_000_000),
            ("max_session_minutes", "Oturum etkin süresi (dk)", 1, 10_080),
            ("max_command_seconds", "Komut zaman aşımı (sn)", 1, 86_400),
        ]
        limits = store.get_limits()
        for key, label, minimum, maximum in labels:
            spin = QSpinBox()
            spin.setRange(minimum, maximum)
            spin.setGroupSeparatorShown(True)
            spin.setValue(getattr(limits, key))
            self.spins[key] = spin
            form.addRow(label, spin)
        limits_layout.addLayout(form)
        root.addWidget(limits_card)

        policy_card = QFrame()
        policy_card.setObjectName("card")
        policy_layout = QVBoxLayout(policy_card)
        policy_layout.setContentsMargins(16, 14, 16, 14)
        policy_layout.setSpacing(9)
        policy_title = QLabel("POLİTİKA")
        policy_title.setStyleSheet("font-weight:700; letter-spacing:1px;")
        policy_layout.addWidget(policy_title)
        policy = store.get_policy()
        self.locked_check = QCheckBox("Ajan eylemlerini kilitle (model/terminal/dosya araçları devre dışı)")
        self.locked_check.setChecked(policy["locked"])
        self.override_check = QCheckBox("Geliştirici modu: kota eşiklerini geçersiz kıl")
        self.override_check.setChecked(policy["developer_override"])
        policy_layout.addWidget(self.locked_check)
        policy_layout.addWidget(self.override_check)
        warning = QLabel("Geliştirici modu istek/token/yürütme kotalarını atlar; genel kilidi atlamaz. Yerel yönetici kontrolleri makine sahibine karşı kurcalamaya dayanıklı değildir.")
        warning.setObjectName("muted")
        warning.setWordWrap(True)
        policy_layout.addWidget(warning)
        root.addWidget(policy_card)

        buttons_row = QHBoxLayout()
        reset_button = QPushButton("Kullanım sayaçlarını sıfırla")
        reset_button.setObjectName("danger")
        reset_button.clicked.connect(self._reset_usage)
        pin_button = QPushButton("Yönetici parolasını değiştir")
        pin_button.clicked.connect(self._change_pin)
        refresh_button = QPushButton("Yenile")
        refresh_button.clicked.connect(self._refresh_stats)
        buttons_row.addWidget(reset_button)
        buttons_row.addWidget(pin_button)
        buttons_row.addWidget(refresh_button)
        root.addLayout(buttons_row)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_button = QPushButton("Ayarları kaydet")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save)
        save_row.addWidget(self.save_button)
        root.addLayout(save_row)
        root.addStretch(1)
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        daily, _, _ = self.store.snapshot()
        self.stats_label.setText(
            f"Tarih: {daily.day}\n"
            f"İstek: {daily.requests:,}  ·  Token: {daily.total_tokens:,} "
            f"(girdi {daily.prompt_tokens:,} / çıktı {daily.completion_tokens:,})\n"
            f"Komut yürütme: {daily.executions:,}  ·  Ajan etkin süresi: {daily.active_seconds / 60:.1f} dk\n"
            "Oturum tokenı ve süresi ana pencerede etkin sohbet için görüntülenir."
        )

    def _save(self) -> None:
        try:
            limits = UsageLimits(**{key: spin.value() for key, spin in self.spins.items()})
            limits.validate()
            if self.override_check.isChecked() and not self.store.get_policy()["developer_override"]:
                answer = QMessageBox.warning(
                    self, "Geliştirici modu", "Kota denetimi devre dışı bırakılacak. Bu ayarı etkinleştirmek istiyor musunuz?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self.store.set_limits(limits)
            self.store.set_policy(locked=self.locked_check.isChecked(), developer_override=self.override_check.isChecked())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Ayarlar kaydedilemedi", str(exc))
            return
        QMessageBox.information(self, "Kaydedildi", "Kota ve politika ayarları güncellendi.")
        self._refresh_stats()

    def _reset_usage(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Sayaçları sıfırla",
            "Tüm güncel ve geçmiş kullanım sayaçları silinecek. Sohbet geçmişi korunur. Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.reset_usage()
        self._refresh_stats()

    def _change_pin(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Yönetici parolasını değiştir")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        current = QLineEdit()
        current.setEchoMode(QLineEdit.EchoMode.Password)
        new = QLineEdit()
        new.setEchoMode(QLineEdit.EchoMode.Password)
        confirm = QLineEdit()
        confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Mevcut parola", current)
        form.addRow("Yeni parola", new)
        form.addRow("Yeni parola (tekrar)", confirm)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self.store.verify_admin_pin(current.text()):
            QMessageBox.warning(self, "Doğrulama başarısız", "Mevcut parola yanlış.")
            return
        if len(new.text()) < 8 or new.text() != confirm.text():
            QMessageBox.warning(self, "Yeni parola geçersiz", "Parola en az 8 karakter olmalı ve tekrar alanıyla eşleşmeli.")
            return
        try:
            self.store.set_admin_pin(new.text())
            QMessageBox.information(self, "Parola güncellendi", "Yönetici parolası güncellendi.")
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Parola güncellenemedi", str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("UsageLimitEditor")
    app.setStyleSheet(ADMIN_STYLE)
    store = UsageStore()
    gate = PinGateDialog(store)
    if gate.exec() != QDialog.DialogCode.Accepted:
        return 0
    window = UsageEditorWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
