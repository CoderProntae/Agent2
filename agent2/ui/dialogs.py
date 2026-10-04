"""Settings dialog for local Ollama preferences and secure GitHub credentials."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from agent2.core.config import AppConfig, SecretStore
from agent2.core.usage import UsageStore
from .workers import ModelListWorker


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, usage: UsageStore, parent=None) -> None:
        super().__init__(parent)
        self.usage = usage
        self.setWindowTitle("Ayarlar · Agent2")
        self.setMinimumWidth(540)
        self.result_config = config
        self._model_worker: ModelListWorker | None = None

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.url_input = QLineEdit(config.ollama_url)
        self.url_input.setPlaceholderText("http://localhost:11435")
        self.model_input = QComboBox()
        self.model_input.setEditable(True)
        self.model_input.addItem(config.model)
        self.model_input.setCurrentText(config.model)
        self.model_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.refresh_models_button = QPushButton("Modelleri yenile")
        self.model_status = QLabel("Varsayılan Ollama adresi: localhost:11435")
        self.model_status.setWordWrap(True)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_input, 1)
        model_row.addWidget(self.refresh_models_button)
        form.addRow("Ollama temel adresi", self.url_input)
        form.addRow("Model", model_row)
        form.addRow("Bağlantı", self.model_status)

        self.repository_input = QLineEdit(config.github_repository)
        self.repository_input.setPlaceholderText("https://github.com/sahip/depo")
        self.branch_input = QLineEdit(config.github_branch)
        self.branch_input.setPlaceholderText("boş = geçerli yerel dal")
        form.addRow("GitHub deposu", self.repository_input)
        form.addRow("Eşitleme dalı", self.branch_input)
        root.addLayout(form)

        token_title = QLabel("GitHub erişim tokenı")
        token_title.setObjectName("sectionTitle")
        root.addWidget(token_title)
        token_row = QHBoxLayout()
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("Token yalnızca işletim sistemi anahtarlığına kaydedilir")
        self.save_token_button = QPushButton("Güvenli kaydet")
        self.remove_token_button = QPushButton("Sil")
        token_row.addWidget(self.token_input, 1)
        token_row.addWidget(self.save_token_button)
        token_row.addWidget(self.remove_token_button)
        root.addLayout(token_row)
        self.token_status = QLabel("")
        self.token_status.setWordWrap(True)
        root.addWidget(self.token_status)
        self._refresh_token_status()

        note = QLabel("Token düz metin ayar dosyasına yazılmaz. Anahtarlık yoksa GitHub eşitlemesi kapalı kalır.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.refresh_models_button.clicked.connect(self._refresh_models)
        self.save_token_button.clicked.connect(self._save_token)
        self.remove_token_button.clicked.connect(self._remove_token)

    def _refresh_token_status(self) -> None:
        try:
            token = SecretStore.get_github_token()
            self.token_status.setText("✓ GitHub tokenı güvenli anahtarlıkta kayıtlı." if token else "Henüz kayıtlı token yok.")
        except RuntimeError as exc:
            self.token_status.setText(f"Anahtarlık kullanılamıyor: {exc}")

    def _save_token(self) -> None:
        token = self.token_input.text().strip()
        try:
            SecretStore.save_github_token(token)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Token kaydedilemedi", str(exc))
            return
        self.token_input.clear()
        self._refresh_token_status()

    def _remove_token(self) -> None:
        answer = QMessageBox.question(self, "Tokenı sil", "Kayıtlı GitHub tokenı işletim sistemi anahtarlığından silinsin mi?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            SecretStore.delete_github_token()
        except RuntimeError as exc:
            QMessageBox.warning(self, "Token silinemedi", str(exc))
        self._refresh_token_status()

    def _refresh_models(self) -> None:
        if self._model_worker and self._model_worker.isRunning():
            return
        self.refresh_models_button.setEnabled(False)
        self.model_status.setText("Ollama modelleri aranıyor…")
        self._model_worker = ModelListWorker(self.url_input.text().strip(), self.usage, self)
        self._model_worker.completed.connect(self._models_loaded)
        self._model_worker.failed.connect(self._models_failed)
        self._model_worker.finished.connect(lambda: self.refresh_models_button.setEnabled(True))
        self._model_worker.start()

    def _models_loaded(self, models: list[dict]) -> None:
        current = self.model_input.currentText().strip()
        names = [str(model.get("name", "")).strip() for model in models if model.get("name")]
        self.model_input.clear()
        self.model_input.addItems(names)
        if current and self.model_input.findText(current) < 0:
            self.model_input.addItem(current)
        self.model_input.setCurrentText(current)
        self.model_status.setText(f"{len(names)} model bulundu." if names else "Bağlantı kuruldu, kurulu model bulunamadı.")

    def _models_failed(self, message: str) -> None:
        self.model_status.setText(f"Bağlantı kurulamadı: {message}")

    def _wait_for_model_worker(self) -> bool:
        if not self._model_worker or not self._model_worker.isRunning():
            return True
        self.model_status.setText("Model listesi isteği tamamlanana kadar bekleniyor…")
        self._model_worker.wait(28_000)
        if self._model_worker.isRunning():
            QMessageBox.warning(self, "Bağlantı sürüyor", "Ollama isteği henüz sonlanmadı; birkaç saniye sonra tekrar deneyin.")
            return False
        return True

    def accept(self) -> None:
        if self._wait_for_model_worker():
            super().accept()

    def reject(self) -> None:
        if self._wait_for_model_worker():
            super().reject()

    def closeEvent(self, event) -> None:
        if self._wait_for_model_worker():
            event.accept()
        else:
            event.ignore()

    def _save(self) -> None:
        config = AppConfig(
            ollama_url=self.url_input.text().strip(),
            model=self.model_input.currentText().strip(),
            workspace=self.result_config.workspace,
            github_repository=self.repository_input.text().strip(),
            github_branch=self.branch_input.text().strip(),
        )
        try:
            config.validate()
            config.save()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Ayarlar geçersiz", str(exc))
            return
        self.result_config = config
        self.accept()
