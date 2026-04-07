# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/settings_page.py
# Página de configurações gerais com seletor de tema.

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QCheckBox, QFrame, QMessageBox, QComboBox
)
from PySide6.QtCore import Qt
from gui import themes

logger = logging.getLogger(__name__)


class SettingsPage(QWidget):
    def __init__(self, config, on_theme_changed=None, parent=None):
        super().__init__(parent)
        self.config = config
        self._on_theme_changed = on_theme_changed
        self.setStyleSheet(themes.settings_page_style())
        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        title = QLabel("Configuracoes")
        title.setProperty("class", "page-title")
        layout.addWidget(title)

        # ── Aparência ──
        theme_group = QFrame()
        theme_group.setProperty("class", "settings-group")
        theme_layout = QVBoxLayout(theme_group)

        theme_title = QLabel("Aparencia")
        theme_title.setProperty("class", "section-title")
        theme_layout.addWidget(theme_title)

        row_theme = QHBoxLayout()
        row_theme.addWidget(QLabel("Tema:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(themes.get_theme_names())
        self.theme_combo.setMaximumWidth(200)
        row_theme.addWidget(self.theme_combo)
        row_theme.addStretch()
        theme_layout.addLayout(row_theme)

        layout.addWidget(theme_group)

        # ── MT5 Settings ──
        mt5_group = QFrame()
        mt5_group.setProperty("class", "settings-group")
        mt5_layout = QVBoxLayout(mt5_group)

        mt5_title = QLabel("MetaTrader 5")
        mt5_title.setProperty("class", "section-title")
        mt5_layout.addWidget(mt5_title)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Caminho base MT5:"))
        self.mt5_path_edit = QLineEdit()
        row1.addWidget(self.mt5_path_edit, 1)
        mt5_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Intervalo monitor (s):"))
        self.monitor_interval_spin = QSpinBox()
        self.monitor_interval_spin.setRange(5, 120)
        self.monitor_interval_spin.setValue(10)
        row2.addWidget(self.monitor_interval_spin)
        row2.addStretch()
        mt5_layout.addLayout(row2)

        layout.addWidget(mt5_group)

        # ── App Settings ──
        app_group = QFrame()
        app_group.setProperty("class", "settings-group")
        app_layout = QVBoxLayout(app_group)

        app_title = QLabel("Aplicacao")
        app_title.setProperty("class", "section-title")
        app_layout.addWidget(app_title)

        self.splash_check = QCheckBox("Exibir Splash Screen ao iniciar")
        app_layout.addWidget(self.splash_check)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Nivel de log:"))
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.log_level_combo.setMaximumWidth(200)
        row3.addWidget(self.log_level_combo)
        row3.addStretch()
        app_layout.addLayout(row3)

        layout.addWidget(app_group)

        # ── CopyTrade Settings ──
        ct_group = QFrame()
        ct_group.setProperty("class", "settings-group")
        ct_layout = QVBoxLayout(ct_group)

        ct_title = QLabel("CopyTrade")
        ct_title.setProperty("class", "section-title")
        ct_layout.addWidget(ct_title)

        row_magic = QHBoxLayout()
        row_magic.addWidget(QLabel("Magic Number:"))
        self.magic_number_spin = QSpinBox()
        self.magic_number_spin.setRange(1, 2147483647)
        self.magic_number_spin.setValue(123456789)
        self.magic_number_spin.setMaximumWidth(200)
        self.magic_number_spin.setToolTip(
            "Identifica trades do CopyTrade vs manuais.\n"
            "Alterar com posicoes abertas pode afetar o rastreamento."
        )
        row_magic.addWidget(self.magic_number_spin)
        row_magic.addStretch()
        ct_layout.addLayout(row_magic)

        row_hb = QHBoxLayout()
        row_hb.addWidget(QLabel("Heartbeat (s):"))
        self.heartbeat_spin = QSpinBox()
        self.heartbeat_spin.setRange(1, 600)
        self.heartbeat_spin.setValue(5)
        self.heartbeat_spin.setMaximumWidth(200)
        self.heartbeat_spin.setToolTip(
            "Intervalo de heartbeat do EA (em segundos).\n"
            "Valores menores = deteccao mais rapida, mais trafego."
        )
        row_hb.addWidget(self.heartbeat_spin)
        row_hb.addStretch()
        ct_layout.addLayout(row_hb)

        layout.addWidget(ct_group)

        layout.addStretch()

        # Save button
        save_btn = QPushButton("Salvar")
        save_btn.setProperty("class", "save-btn")
        save_btn.setMaximumWidth(200)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn, alignment=Qt.AlignRight)

    def _load_settings(self):
        self.mt5_path_edit.setText(
            self.config.get('General', 'base_mt5_path', fallback='C:/Temp/MT5')
        )
        self.monitor_interval_spin.setValue(
            self.config.getint('General', 'monitor_interval', fallback=10)
        )
        self.splash_check.setChecked(
            self.config.getboolean('General', 'show_splash', fallback=True)
        )
        saved_log_level = self.config.get('General', 'log_level', fallback='INFO').upper()
        idx_log = self.log_level_combo.findText(saved_log_level)
        if idx_log >= 0:
            self.log_level_combo.setCurrentIndex(idx_log)
        # Tema
        saved_theme = self.config.get('GUI', 'theme', fallback='Escuro')
        idx = self.theme_combo.findText(saved_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        # CopyTrade
        self.magic_number_spin.setValue(
            self.config.getint('CopyTrade', 'magic_number', fallback=123456789)
        )
        self.heartbeat_spin.setValue(
            self.config.getint('CopyTrade', 'heartbeat_interval', fallback=5)
        )

    def apply_theme(self):
        self.setStyleSheet(themes.settings_page_style())

    def _save_settings(self):
        try:
            self.config.set('General', 'base_mt5_path', self.mt5_path_edit.text())
            self.config.set('General', 'monitor_interval', str(self.monitor_interval_spin.value()))
            self.config.set('General', 'show_splash', str(self.splash_check.isChecked()))
            self.config.set('General', 'log_level', self.log_level_combo.currentText())

            # Salvar e aplicar tema
            new_theme = self.theme_combo.currentText()
            self.config.set('GUI', 'theme', new_theme)
            themes.set_theme(new_theme)
            if self._on_theme_changed:
                self._on_theme_changed()

            # CopyTrade
            self.config.set('CopyTrade', 'magic_number', str(self.magic_number_spin.value()))
            self.config.set('CopyTrade', 'heartbeat_interval', str(self.heartbeat_spin.value()))

            self.config.save_config()
            QMessageBox.information(self, "Sucesso", "Configuracoes salvas.\n\nAlteracoes de CopyTrade serao aplicadas na proxima conexao dos EAs.")
            logger.info("Configuracoes salvas.")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao salvar: {e}")
            logger.error(f"Erro ao salvar configuracoes: {e}")
