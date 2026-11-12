"""One online connection editor used by launcher and overlay (no network on UI thread)."""
from __future__ import annotations

import threading
import math
import copy
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QApplication, QDialog, QFormLayout, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                            QLineEdit, QPushButton, QSpinBox, QMessageBox, QDialogButtonBox, QStyle)

from localization import tr
from online_credentials import CREDENTIALS, CredentialError
from online_translation import (ONLINE, PROVIDERS, OnlineError, connection_id, normalize_settings,
                                validate_configuration, validate_connection, AZURE_ENDPOINTS, GIGA_SCOPES, baidu_credentials,
                                provider_settings, valid_custom_model, MIN_TIMEOUT_S, MAX_TIMEOUT_S)


class OnlineSettingsDialog(QDialog):
    def __init__(self, parent=None, settings=None, language="uk", source="en", target="uk"):
        super().__init__(parent)
        self.language, self.source, self.target = language, source, target
        self.result_settings = normalize_settings(settings)
        self._provider_bank = copy.deepcopy(self.result_settings["providers"])
        self._current_custom_models = []
        self.setWindowTitle(tr(language, "online_settings"))
        self.setMinimumWidth(400)
        self._cancelled = threading.Event()
        self._revision = 0
        self._test_result = None
        self._testing = False
        self._authorized_key = None
        self._credential_snapshot = None
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)
        self.provider = QComboBox()
        for key, label in (("google", "Google (deep_translator)"), ("deepl", "DeepL API"), ("qwen_mt", "Alibaba Qwen-MT"),
                           ("gemini", tr(language, "online_provider_gemini")), ("openai", tr(language, "online_provider_openai")),
                           ("yandex", tr(language, "online_provider_yandex"))):
            self.provider.addItem(label, key)
        self.model = QComboBox()
        for key, label in (("deepseek", "DeepSeek"), ("azure", "Azure Translator"),
                           ("google_cloud", "Google Cloud Translation"), ("baidu", "Baidu Translate"),
                           ("gigachat", "GigaChat"), ("openrouter", "OpenRouter")):
            self.provider.addItem(label, key)
        self.region = QComboBox()
        self.region.addItems(PROVIDERS["qwen_mt"].regions)
        self.workspace = QLineEdit()
        self.workspace.setMaxLength(63)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(4096)
        self.appid = QLineEdit()
        self.appid.setMaxLength(64)
        self.appid.setEchoMode(QLineEdit.EchoMode.Password)
        self.endpoint = QComboBox()
        self.endpoint.addItems(AZURE_ENDPOINTS)
        self.scope = QComboBox()
        self.scope.addItems(GIGA_SCOPES)
        self.ca_bundle = QLineEdit()
        self.ca_bundle.setMaxLength(240)
        self.rpm = QSpinBox()
        self.rpm.setRange(1, 600)
        self.live_timeout = QSpinBox()
        self.dynamic_timeout = QSpinBox()
        for widget, key in ((self.live_timeout, "live_timeout_s"), (self.dynamic_timeout, "dynamic_timeout_s")):
            widget.setRange(MIN_TIMEOUT_S, MAX_TIMEOUT_S)
            widget.setValue(self.result_settings[key])
        for name, widget in (("provider", self.provider), ("model", self.model), ("region", self.region),
                             ("workspace", self.workspace), ("endpoint", self.endpoint), ("scope", self.scope),
                             ("ca_bundle", self.ca_bundle), ("appid", self.appid), ("key", self.key), ("rpm", self.rpm),
                             ("live_timeout", self.live_timeout), ("dynamic_timeout", self.dynamic_timeout)):
            form.addRow(tr(language, "online_" + name), widget)
        self.form = form
        models = QHBoxLayout()
        self.add_model = QPushButton()
        self.remove_model = QPushButton()
        for button, icon, key in ((self.add_model, QStyle.StandardPixmap.SP_FileDialogNewFolder, "add_model"),
                                  (self.remove_model, QStyle.StandardPixmap.SP_TrashIcon, "remove_model")):
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(tr(language, "online_" + key))
            button.setAccessibleName(tr(language, "online_" + key))
            button.setFixedSize(28, 26)
            models.addWidget(button)
        models.addStretch()
        outer.addLayout(models)
        keys = QHBoxLayout()
        self.save_key = QPushButton(tr(language, "online_save_key"))
        self.delete_key = QPushButton(tr(language, "online_delete_key"))
        keys.addWidget(self.save_key)
        keys.addWidget(self.delete_key)
        outer.addLayout(keys)
        credentials_row = QHBoxLayout()
        self.credential_icon = QLabel()
        self.credential_icon.setFixedSize(20, 20)
        self.credential_status = QLabel()
        self.credential_status.setWordWrap(True)
        credentials_row.addWidget(self.credential_icon)
        credentials_row.addWidget(self.credential_status, 1)
        outer.addLayout(credentials_row)
        self.warning = QLabel(tr(language, "online_google_warning"))
        self.warning.setWordWrap(True)
        outer.addWidget(self.warning)
        self.test_button = QPushButton(tr(language, "online_test"))
        outer.addWidget(self.test_button)
        self.check_key_button = QPushButton(tr(language, "online_check_key"))
        outer.addWidget(self.check_key_button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.statistics = QLabel()
        self.statistics.setWordWrap(True)
        outer.addWidget(self.statistics)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr(language, "save_and_close"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr(language, "cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.result_settings["provider"])))
        self.rebuild_models()
        self.model.setCurrentIndex(max(0, self.model.findText(self.result_settings["model"])))
        if self.model.isEditable():
            self.model.setCurrentText(self.result_settings["model"])
        self.region.setCurrentText(self.result_settings["region"])
        self.workspace.setText(self.result_settings["workspace"])
        self.rpm.setValue(self.result_settings["requests_per_minute"])
        self.endpoint.setCurrentText(self.result_settings.get("endpoint", "global"))
        self.scope.setCurrentText(self.result_settings.get("scope", GIGA_SCOPES[0]))
        self.ca_bundle.setText(self.result_settings.get("ca_bundle", ""))
        self._provider_id = self.provider.currentData()
        self.provider.currentIndexChanged.connect(self.provider_changed)
        for signal in (self.model.currentTextChanged, self.region.currentTextChanged,
                       self.workspace.textChanged, self.key.textChanged, self.rpm.valueChanged,
                       self.appid.textChanged, self.endpoint.currentIndexChanged, self.scope.currentIndexChanged,
                       self.ca_bundle.textChanged, self.live_timeout.valueChanged, self.dynamic_timeout.valueChanged):
            signal.connect(self.reset_status)
        self.save_key.clicked.connect(self.store_key)
        self.delete_key.clicked.connect(self.remove_key)
        self.test_button.clicked.connect(self.test_connection)
        self.check_key_button.clicked.connect(self.test_key)
        self.add_model.clicked.connect(self.add_custom_model)
        self.remove_model.clicked.connect(self.remove_custom_model)
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.poll_result)
        self.reset_status()

    def _capture_provider(self, provider):
        return {"provider": provider, "model": self.model.currentText(),
            "region": self.region.currentText(), "workspace": self.workspace.text(),
            "endpoint": self.endpoint.currentText(), "scope": self.scope.currentText(), "ca_bundle": self.ca_bundle.text(),
            "custom_models": list(self._current_custom_models), "requests_per_minute": self.rpm.value()}

    def settings(self):
        return normalize_settings({**self._capture_provider(self.provider.currentData()),
            "providers": copy.deepcopy(self._provider_bank), "live_timeout_s": self.live_timeout.value(),
            "dynamic_timeout_s": self.dynamic_timeout.value()})

    def rebuild_models(self):
        definition = PROVIDERS[self.provider.currentData()]
        provider = definition.provider_id
        cfg = self._provider_bank.get(provider) or provider_settings({}, provider)
        controls = (self.model, self.region, self.workspace, self.endpoint, self.scope, self.ca_bundle, self.rpm)
        for widget in controls:
            widget.blockSignals(True)
        self.check_key_button.setVisible(provider == "openrouter")
        self.model.setEditable(definition.editable_models)
        self.model.clear()
        self.model.addItems(definition.models)
        self._current_custom_models = list(cfg.get("custom_models") or [])
        self.model.addItems(self._current_custom_models)
        self.model.setEnabled(len(definition.models) > 1 or definition.editable_models)
        self.add_model.setVisible(definition.editable_models)
        self.remove_model.setVisible(definition.editable_models)
        if definition.editable_models:
            self.model.lineEdit().setMaxLength(160)
        self.region.setEditable(provider == "azure")
        self.region.clear()
        self.region.addItems(("",) if provider == "azure" else PROVIDERS["qwen_mt"].regions)
        self.region.setEnabled(provider in {"qwen_mt", "azure"})
        self.workspace.setEnabled(provider == "qwen_mt")
        for widget, enabled in ((self.endpoint, provider == "azure"), (self.scope, provider == "gigachat"),
                                (self.ca_bundle, provider == "gigachat"), (self.appid, provider == "baidu")):
            widget.setVisible(enabled)
            self.form.labelForField(widget).setVisible(enabled)
        self.form.labelForField(self.key).setText(tr(self.language, "online_authorization_key" if provider == "gigachat"
            else "online_secret" if provider == "baidu" else "online_key"))
        for widget in (self.key, self.save_key, self.delete_key):
            widget.setEnabled(definition.requires_key)
        self.warning.setText(tr(self.language, "online_google_warning" if not definition.requires_key else "online_paid_warning"))
        if definition.provider_id == "yandex" and self.target == "zh_hant":
            self.warning.setText(tr(self.language, "online_yandex_script_warning"))
        if provider in {"baidu", "gigachat", "openrouter", "azure"}:
            self.warning.setText(tr(self.language, "online_" + provider + "_warning"))
        self.model.setCurrentText(cfg["model"])
        self.region.setCurrentText(cfg["region"])
        self.workspace.setText(cfg["workspace"])
        self.endpoint.setCurrentText(cfg.get("endpoint", "global"))
        self.scope.setCurrentText(cfg.get("scope", GIGA_SCOPES[0]))
        self.ca_bundle.setText(cfg.get("ca_bundle", ""))
        self.rpm.setValue(cfg["requests_per_minute"])
        for widget in controls:
            widget.blockSignals(False)
        self.reset_status()

    def provider_changed(self):
        if self.key.text() or self.appid.text():
            self.provider.blockSignals(True)
            self.provider.setCurrentIndex(self.provider.findData(self._provider_id))
            self.provider.blockSignals(False)
            self.status.setText(tr(self.language, "online_unsaved_credentials"))
            return
        previous = self._provider_id
        previous_cfg = normalize_settings(self._capture_provider(previous))
        self._provider_bank[previous] = previous_cfg["providers"][previous]
        self._provider_id = self.provider.currentData()
        self.rebuild_models()

    def add_custom_model(self):
        provider, model = self.provider.currentData(), self.model.currentText().strip()
        if not PROVIDERS[provider].editable_models or not valid_custom_model(provider, model):
            self.show_error(OnlineError("unsupported_model"))
            return False
        if model not in self._current_custom_models:
            if len(self._current_custom_models) >= 64:
                self.show_error(OnlineError("invalid_configuration"))
                return False
            self._current_custom_models.append(model)
            self.model.addItem(model)
        self.model.setCurrentText(model)
        self.reset_status()
        return True

    def remove_custom_model(self):
        model = self.model.currentText()
        if model in self._current_custom_models:
            self._current_custom_models.remove(model)
            self.model.removeItem(self.model.findText(model))
            self.reset_status()

    def update_credential_status(self):
        cfg = self.settings()
        if not PROVIDERS[cfg["provider"]].requires_key:
            state = "not_required"
        else:
            connection = connection_id(cfg)
            revision = CREDENTIALS.revision(connection)
            stamp = (connection, revision)
            if self._credential_snapshot is None or self._credential_snapshot[:2] != stamp:
                self._credential_snapshot = (*stamp, CREDENTIALS.status(connection))
            state = self._credential_snapshot[2]
            if state == "saved" and self._authorized_key == stamp:
                state = "authorized"
        self.credential_status.setText(tr(self.language, "online_key_" + state))
        color = "#b3261e" if state in {"missing", "unavailable"} else "#237442" if state in {"saved", "authorized"} else "#555555"
        if QApplication.instance().property("launcherDarkUi"):
            from launcher_style import MUTED, STATUS
            color = (STATUS["error"] if state in {"missing", "unavailable"}
                     else STATUS["success"] if state in {"saved", "authorized"} else MUTED)
        icon = (QStyle.StandardPixmap.SP_MessageBoxWarning if state in {"missing", "unavailable"}
                else QStyle.StandardPixmap.SP_DialogApplyButton if state == "authorized"
                else QStyle.StandardPixmap.SP_DialogSaveButton if state == "saved" else QStyle.StandardPixmap.SP_MessageBoxInformation)
        self.credential_status.setStyleSheet("color: " + color + ";")
        self.credential_icon.setPixmap(self.style().standardIcon(icon).pixmap(18, 18))
        self.credential_icon.setToolTip(self.credential_status.text())
        self.credential_icon.setAccessibleName(self.credential_status.text())
        self.credential_status.setProperty("credentialState", state)

    def reset_status(self, *_args):
        self._revision += 1
        self.status.setText(tr(self.language, "online_status_unverified"))
        self.update_credential_status()
        self.update_statistics()

    def update_statistics(self):
        if hasattr(self, "statistics"):
            self.statistics.setText(tr(self.language, "online_statistics", requests=ONLINE.counters["requests"],
                input=ONLINE.counters["prompt_tokens"], output=ONLINE.counters["completion_tokens"],
                characters=ONLINE.counters["billed_characters"]))

    def store_key(self):
        try:
            cfg = validate_connection(self.settings())
            if cfg["provider"] == "baidu" and (not self.appid.text().strip() or not self.key.text().strip()):
                self.status.setText(tr(self.language, "online_baidu_credentials_required"))
                return False
            secret = baidu_credentials(self.appid.text(), self.key.text()) if cfg["provider"] == "baidu" else self.key.text()
            CREDENTIALS.put(connection_id(cfg), secret)
            self._authorized_key = None
            self._credential_snapshot = None
            self.key.clear()
            self.appid.clear()
            self.reset_status()
            self.credential_status.setText(tr(self.language, "online_key_saved_confirmation"))
            return True
        except OnlineError as exc:
            self.status.setText(tr(self.language, "online_error_" + exc.code))
        except (CredentialError, OSError):
            self.status.setText(tr(self.language, "online_error_credential_unavailable"))
        return False

    def remove_key(self):
        try:
            CREDENTIALS.delete(connection_id(self.settings()))
            self._authorized_key = None
            self._credential_snapshot = None
            self.key.clear()
            self.appid.clear()
            self.reset_status()
            self.status.setText(tr(self.language, "online_key_deleted"))
        except OSError:
            self.status.setText(tr(self.language, "online_error_credential_unavailable"))

    def test_connection(self):
        self.start_test("translation")

    def test_key(self):
        self.start_test("authorization")

    def start_test(self, operation):
        if self._testing:
            return
        if (self.key.text() or self.appid.text()) and not self.store_key():
            return
        cfg = self.settings()
        try:
            if operation == "authorization":
                if cfg["provider"] != "openrouter":
                    raise OnlineError("invalid_configuration")
                from online_translation import require_credentials
                require_credentials(cfg, CREDENTIALS)
            else:
                validate_configuration(cfg, self.source, self.target, credentials=CREDENTIALS)
        except OnlineError as exc:
            self.show_error(exc)
            return
        if operation == "translation" and PROVIDERS[cfg["provider"]].requires_key and QMessageBox.question(self,
                tr(self.language, "online_test"), tr(self.language, "online_paid_warning")) != QMessageBox.StandardButton.Yes:
            return
        self._testing = True
        self.test_button.setEnabled(False)
        self.check_key_button.setEnabled(False)
        self.status.setText(tr(self.language, "online_testing"))
        revision = self._revision
        credential_stamp = (connection_id(cfg), CREDENTIALS.revision(connection_id(cfg)))
        cancelled = self._cancelled
        source, target = self.source, self.target
        text = {"ja": "こんにちは。", "zh": "你好。", "zh_hant": "你好。"}.get(source, "Hello.")
        def run():
            error = None
            try:
                if operation == "authorization":
                    ONLINE.check_key(cfg, cancelled=cancelled.is_set)
                else:
                    result = ONLINE.translate(text, source, target, cfg, cancelled=cancelled.is_set, explicit_test=True)
                    if not result.text.strip() or result.text.strip() == text:
                        error = OnlineError("invalid_response", origin="remote")
            except OnlineError as exc:
                error = exc
            except Exception:
                error = OnlineError("network", origin="transport")
            self._test_result = revision, operation, error, credential_stamp
        threading.Thread(target=run, name="online-connection-test", daemon=True).start()
        self.timer.start()

    def poll_result(self):
        if self._test_result is None:
            return
        revision, operation, error, credential_stamp = self._test_result
        self._test_result = None
        self._testing = False
        self.test_button.setEnabled(True)
        self.check_key_button.setEnabled(True)
        self.timer.stop()
        self.update_statistics()
        if revision == self._revision and credential_stamp == (connection_id(self.settings()), CREDENTIALS.revision(connection_id(self.settings()))):
            if error:
                if error.code in {"invalid_key", "credential_missing", "credential_unavailable"}:
                    self._authorized_key = None
                    self.update_credential_status()
                self.show_error(error)
            elif operation == "authorization":
                self._authorized_key = credential_stamp
                self.update_credential_status()
                self.status.setText(tr(self.language, "online_openrouter_authorized"))
            else:
                if PROVIDERS[self.settings()["provider"]].requires_key:
                    self._authorized_key = credential_stamp
                    self.update_credential_status()
                self.status.setText(tr(self.language, "online_status_success"))

    def show_error(self, error):
        parts = [tr(self.language, "online_error_" + error.code), tr(self.language, "online_origin_" + error.origin)]
        if error.http_status is not None:
            parts.append("HTTP " + str(error.http_status))
        if error.provider_code:
            parts.append(tr(self.language, "online_provider_error_code", code=error.provider_code))
        if error.retry_after_s is not None:
            parts.append(tr(self.language, "online_retry_after", seconds=math.ceil(error.retry_after_s)))
        self.status.setText("; ".join(parts))

    def done(self, result):
        self._cancelled.set()
        self.timer.stop()
        super().done(result)

    def accept(self):
        if (self.key.text() or self.appid.text()) and not self.store_key():
            return
        if (PROVIDERS[self.provider.currentData()].editable_models and self.model.currentText().strip()
                and not self.add_custom_model()):
            return
        self.result_settings = self.settings()
        super().accept()
