"""Typed client choices with readable labels and preservation of legacy values."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QSizePolicy

from core.i18n import tr
from .presentation import Choice
from .devices import visible_inputs

# Canonical keys in the server language mapping, without importing server startup.
RECOGNITION_LANGUAGES = (
    'auto', 'chinese', 'english', 'cantonese', 'japanese', 'korean', 'arabic', 'german',
    'french', 'spanish', 'portuguese', 'indonesian', 'italian', 'russian', 'thai',
    'vietnamese', 'turkish', 'hindi', 'malay', 'dutch', 'swedish', 'danish', 'finnish',
    'polish', 'czech', 'filipino', 'persian', 'greek', 'romanian', 'hungarian', 'macedonian',
)


class ConfigChoice(Choice):
    def __init__(self):
        super().__init__()
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(10)
        self.setMaxVisibleItems(12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def find_value(self, value):
        return next((index for index in range(self.count())
                     if type(self.itemData(index)) is type(value) and self.itemData(index) == value), -1)

    def set_config_value(self, value):
        index = self.find_value(value)
        if index < 0:
            self.addItem(self.saved_label(value), value)
            index = self.count() - 1
        self.setCurrentIndex(index)

    def saved_label(self, value):
        return tr('gui.saved_choice', value=str(value))


class RecognitionChoice(ConfigChoice):
    def __init__(self):
        super().__init__()
        for code in RECOGNITION_LANGUAGES:
            self.addItem(tr('gui.asr_language.' + code), code)
        longest = max(self.fontMetrics().horizontalAdvance(self.itemText(index)) for index in range(self.count()))
        self.setMinimumWidth(min(260, max(160, longest + 56)))
        self.setMaximumWidth(260)


class MicrophoneChoice(ConfigChoice):
    def __init__(self):
        super().__init__()
        # Leave room for refresh and the fixed status slot on compact screens.
        self.setMinimumWidth(120)
        self.inventory = None
        self.addItem(tr('gui.device_default'), None)

    def set_config_value(self, value):
        if value is None or value == '':
            # Empty legacy defaults retain their type without a duplicate menu item.
            self.setItemData(0, value)
            self.setCurrentIndex(0)
        else:
            super().set_config_value(value)

    def saved_label(self, value):
        if value is None or value == '':
            return tr('gui.device_default')
        for row in (self.inventory or {}).get('devices', []):
            if type(value) is int and row['index'] == value:
                return tr('gui.device_legacy_index', name=row['name'], index=value)
        return tr('gui.device_saved', value=str(value))

    def replace_inventory(self, result):
        if result == self.inventory:
            return
        selected = self.currentData()
        blocked = self.blockSignals(True)
        self.inventory = result
        self.clear()
        default = next((row for row in result['devices'] if row['index'] == result['default']), None)
        self.addItem(tr('gui.device_default_named', name=default['name']) if default
                     else tr('gui.device_default'), None)
        for row in visible_inputs(result):
            self.addItem(row['name'], row['selector'])
            self.setItemData(self.count() - 1, row['api'], Qt.ItemDataRole.ToolTipRole)
            if row['ambiguous']:
                index = self.count() - 1
                self.model().item(index).setEnabled(False)
                self.setItemData(index, tr('gui.device_ambiguous'), Qt.ItemDataRole.ToolTipRole)
        self.set_config_value(selected)
        self.blockSignals(blocked)

    def selection_found(self):
        selected = self.currentData()
        if selected is None or selected == '':
            return True
        rows = (self.inventory or {}).get('devices', [])
        if type(selected) is int:
            return any(selected == row['index'] for row in rows)
        matches = [row for row in rows if selected.casefold() in (row['selector'].casefold(), row['name'].casefold())]
        return len(matches) == 1 and not matches[0]['ambiguous']
