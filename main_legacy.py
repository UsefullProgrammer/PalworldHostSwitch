"""Palworld Save Browser and Host Switch workspace LEGACY.
LEGACY WIL NOT MORE UPDATE!
Safe file operations are enabled by default. The host switch action deliberately
stays disabled until a supported save parser can edit and validate the world
player registry as well as the individual player files.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import contextlib
import io
import re
import uuid
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QInputDialog,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


if getattr(sys, "frozen", False):
    # PyInstaller extracts bundled data into _MEIPASS in one-file mode; in
    # one-folder mode this is the application's _internal directory.
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    APP_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    APP_DIR = RESOURCE_DIR
ROOT = APP_DIR.parent
SAVE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Pal" / "Saved" / "SaveGames"
#SAVE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Pal" / "Saved" / "SaveGames"
OUTPUT_ROOT = ROOT / "SAVEEDITED"
BACKUP_ROOT = ROOT / "backupSAVE"
HOST_ID = "00000000000000000000000000000001"
CONFIG_PATH = APP_DIR / "player_names.json"
SAVE_MAGIC = b"GVAS"
TOOLKIT_PY = RESOURCE_DIR / "palworld-save-toolkit" / "python"
VENDOR_PY = TOOLKIT_PY / "vendor"


def load_toolkit():
    """Import the toolkit's PlM/Oodle-aware parser ahead of any installed copy."""
    if not getattr(sys, "frozen", False):
        for path in (str(TOOLKIT_PY), str(VENDOR_PY)):
            if path not in sys.path:
                sys.path.insert(0, path)
    print(f"TOOLKIT_PY: {TOOLKIT_PY}")
    print(f"VENDOR_PY: {VENDOR_PY}")
    from palworld_save_tools.archive import UUID
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
    from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    return UUID, GvasFile, compress_gvas_to_sav, decompress_sav_to_gvas, PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS


def parse_player_roster(world: Path) -> list[dict[str, str]]:
    """Read player file IDs and names/levels from the world's Level.sav."""
    UUID, GvasFile, _, decompress, custom, hints = load_toolkit()
    players: dict[str, dict[str, str]] = {}
    for file in sorted((world / "Players").glob("*.sav")):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            raw, _ = decompress(file.read_bytes())
            data = GvasFile.read(raw, hints, custom, allow_nan=True).dump()
        sd = data["properties"]["SaveData"]["value"]
        guid = file.stem.upper()
        players[guid] = {"guid": guid, "name": "", "level": "?"}
        instance_id = str(sd["IndividualId"]["value"]["InstanceId"]["value"])
        players[guid]["instance"] = instance_id
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        raw, _ = decompress((world / "Level.sav").read_bytes())
        level = GvasFile.read(raw, hints, custom, allow_nan=True).dump()
    entries = level["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    for entry in entries:
        instance = str(entry["key"]["InstanceId"]["value"])
        for player in players.values():
            if instance == player.get("instance"):
                param = entry["value"]["RawData"]["value"].get("object", {}).get("SaveParameter", {}).get("value", {})
                player["name"] = str(param.get("NickName", {}).get("value", "(nome non trovato)"))
                lvl = param.get("Level", {}).get("value", "?")
                if isinstance(lvl, dict):
                    lvl = lvl.get("value", "?")
                player["level"] = str(lvl)
                break
    return list(players.values())


def _guid_forms(guid32: str) -> tuple[str, bytes]:
    normalized = guid32.strip().replace("-", "").upper()
    if not re.fullmatch(r"[0-9A-F]{32}", normalized):
        raise ValueError("La GUID deve contenere esattamente 32 caratteri esadecimali.")
    dashed = f"{normalized[:8]}-{normalized[8:12]}-{normalized[12:16]}-{normalized[16:20]}-{normalized[20:]}".lower()
    # Palworld's serialized FGuid byte order is UUID.bytes_le, not hex-string order.
    return dashed, uuid.UUID(normalized).bytes_le


def swap_player_profiles(source: Path, output: Path, first_guid: str, second_guid: str) -> str:
    """Exchange two player identities and their world references into a new copy."""
    UUID, GvasFile, compress, decompress, custom, hints = load_toolkit()
    old_guid = first_guid.upper().replace("-", "")
    new_guid = second_guid.upper().replace("-", "")
    if new_guid == old_guid:
        raise ValueError("Le due GUID selezionate devono essere diverse.")
    old_dashed, old_bytes = _guid_forms(old_guid)
    new_dashed, new_bytes = _guid_forms(new_guid)
    old_file, new_file = source / "Players" / f"{old_guid}.sav", source / "Players" / f"{new_guid}.sav"
    if not old_file.is_file() or not new_file.is_file():
        raise ValueError("Non trovo entrambi i file player selezionati nella cartella Players.")
    # Read and parse everything before creating output.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        level_raw, level_type = decompress((source / "Level.sav").read_bytes())
        level = GvasFile.read(level_raw, hints, custom, allow_nan=True)
        player_docs = {}
        for guid, player_file in ((old_guid, old_file), (new_guid, new_file)):
            player_raw, player_type = decompress(player_file.read_bytes())
            player_docs[guid] = (GvasFile.read(player_raw, hints, custom, allow_nan=True), player_type)
    wsd = level.properties["worldSaveData"]["value"]
    char_map = wsd["CharacterSaveParameterMap"]["value"]
    host_doc, host_type = player_docs[old_guid]
    client_doc, client_type = player_docs[new_guid]
    host_data = host_doc.properties["SaveData"]["value"]
    client_data = client_doc.properties["SaveData"]["value"]
    host_instance = str(host_data["IndividualId"]["value"]["InstanceId"]["value"])
    client_instance = str(client_data["IndividualId"]["value"]["InstanceId"]["value"])
    # In co-op saves the palbox/party slot references are tied to the special
    # host slot. Do not exchange these slot owners with the client GUID.
    pal_instances = []
    expected_pal_owners = {}
    for entry in char_map:
        raw = entry["value"]["RawData"]["value"]
        params = raw.get("object", {}).get("SaveParameter", {}).get("value", {}) if isinstance(raw, dict) else {}
        if not params.get("IsPlayer", {}).get("value", False):
            instance = str(entry["key"]["InstanceId"]["value"])
            pal_instances.append(instance)
            owner = str(params.get("OwnerPlayerUId", {}).get("value", ""))
            old_owners = tuple(str(value) for value in params.get("OldOwnerPlayerUIds", {}).get("value", {}).get("values", []))

            def remap_owner(value: str) -> str:
                if value.lower() == old_dashed:
                    return new_dashed
                if value.lower() == new_dashed:
                    return old_dashed
                return value

            expected_pal_owners[instance] = (remap_owner(owner), tuple(remap_owner(value) for value in old_owners))
    original_container_slots = []
    for container in wsd.get("CharacterContainerSaveData", {}).get("value", []):
        for slot in container["value"].get("Slots", {}).get("value", {}).get("values", []):
            slot_raw = slot.get("RawData", {}).get("value", {})
            if isinstance(slot_raw, dict) and "player_uid" in slot_raw:
                original_container_slots.append((str(slot_raw["player_uid"]), str(slot_raw.get("instance_id", ""))))
    host_entry = [e for e in char_map if str(e["key"]["InstanceId"]["value"]) == host_instance]
    client_entry = [e for e in char_map if str(e["key"]["InstanceId"]["value"]) == client_instance]
    if len(host_entry) != 1 or len(client_entry) != 1:
        raise ValueError("Il registro del mondo non contiene un riferimento univoco a entrambi i personaggi.")

    # Exchange the two character file contents, adjusting the embedded player IDs.
    host_data["PlayerUId"]["value"] = new_dashed
    host_data["IndividualId"]["value"]["PlayerUId"]["value"] = new_dashed
    client_data["PlayerUId"]["value"] = old_dashed
    client_data["IndividualId"]["value"]["PlayerUId"]["value"] = old_dashed

    # Keys identify the character bodies; swap their owner GUIDs. Instance IDs stay put.
    host_entry[0]["key"]["PlayerUId"]["value"] = new_dashed
    client_entry[0]["key"]["PlayerUId"]["value"] = old_dashed

    # Swap the player GUID anywhere it is used as a pal owner or previous owner.
    for entry in char_map:
        raw = entry["value"]["RawData"]["value"]
        if not isinstance(raw, dict) or "object" not in raw:
            raise ValueError("Almeno un personaggio non è decodificato: lo switch viene annullato per sicurezza.")
        params = raw["object"].get("SaveParameter", {}).get("value", {})
        owner = params.get("OwnerPlayerUId", {})
        if str(owner.get("value", "")).lower() == old_dashed:
            owner["value"] = new_dashed
        elif str(owner.get("value", "")).lower() == new_dashed:
            owner["value"] = old_dashed
        values = params.get("OldOwnerPlayerUIds", {}).get("value", {}).get("values", [])
        for i, value in enumerate(values):
            if str(value).lower() == old_dashed:
                values[i] = new_dashed
            elif str(value).lower() == new_dashed:
                values[i] = old_dashed

    # Preserve opaque guild payloads, replacing the exact GUID bytes in both directions.
    protected_guild_handles = []
    for group_index, group in enumerate(wsd.get("GroupSaveDataMap", {}).get("value", [])):
        group_value = group["value"]
        raw = group["value"].get("RawData", {}).get("value", {})
        if group_value.get("GroupType", {}).get("value", {}).get("value") != "EPalGroupType::Guild" or "values" not in raw:
            continue
        original_blob = bytes(raw["values"])
        # Palbox guild handles pair a PlayerUId with each pal InstanceId. Keep
        # those handles on the co-op host slot; move the two player references
        # in the rest of the guild payload.
        protected_spans: list[tuple[int, bytes]] = []
        for instance in pal_instances:
            instance_bytes = UUID.from_str(instance).raw_bytes
            offset = 0
            while True:
                offset = original_blob.find(instance_bytes, offset)
                if offset < 16:
                    break
                handle_start = offset - 16
                handle = original_blob[handle_start:offset]
                if handle in (old_bytes, new_bytes):
                    protected_spans.append((handle_start, handle))
                    protected_guild_handles.append((group_index, handle_start, handle))
                offset += len(instance_bytes)
        temp = b"\xfe" * 16
        while temp in original_blob or temp in (old_bytes, new_bytes):
            temp = bytes((x + 1) & 0xFF for x in temp)
        blob = original_blob.replace(old_bytes, temp).replace(new_bytes, old_bytes).replace(temp, new_bytes)
        blob = bytearray(blob)
        for offset, handle in protected_spans:
            blob[offset:offset + 16] = handle
        raw["values"] = list(blob)

    output.mkdir(parents=True, exist_ok=False)
    for item in source.iterdir():
        dest = output / item.name
        if item.name.lower() == "players":
            shutil.copytree(item, dest)
        elif item.is_dir() and item.name.lower() == "backup":
            shutil.copytree(item, dest)
        elif item.is_file():
            shutil.copy2(item, dest)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        level_bytes = compress(level.write(custom), level_type)
        old_player_bytes = compress(client_doc.write(custom), client_type)
        new_player_bytes = compress(host_doc.write(custom), host_type)
    (output / "Level.sav").write_bytes(level_bytes)
    (output / "Players" / f"{old_guid}.sav").write_bytes(old_player_bytes)
    (output / "Players" / f"{new_guid}.sav").write_bytes(new_player_bytes)

    # Reparse output and verify the two character files and world-map owners.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        verify_raw, _ = decompress((output / "Level.sav").read_bytes())
        verify = GvasFile.read(verify_raw, hints, custom, allow_nan=True)
        for guid, player_file in ((old_guid, output / "Players" / f"{old_guid}.sav"), (new_guid, output / "Players" / f"{new_guid}.sav")):
            test_raw, _ = decompress(player_file.read_bytes())
            test_doc = GvasFile.read(test_raw, hints, custom, allow_nan=True)
            embedded = str(test_doc.properties["SaveData"]["value"]["PlayerUId"]["value"]).replace("-", "").upper()
            if embedded != guid:
                raise ValueError(f"Verifica non riuscita per il player {guid}.")
        vwsd = verify.properties["worldSaveData"]["value"]
        vmap = vwsd["CharacterSaveParameterMap"]["value"]
        key_by_instance = {
            str(entry["key"]["InstanceId"]["value"]): str(entry["key"]["PlayerUId"]["value"]).replace("-", "").upper()
            for entry in vmap
        }
        if key_by_instance.get(host_instance) != new_guid:
            raise ValueError("Verifica post-scrittura: il personaggio dell'host non è associato al GUID del client promosso.")
        if key_by_instance.get(client_instance) != old_guid:
            raise ValueError("Verifica post-scrittura: il personaggio del client non è associato al GUID host originale.")
        actual_pal_owners = {}
        for entry in vmap:
            raw = entry["value"]["RawData"]["value"]
            params = raw.get("object", {}).get("SaveParameter", {}).get("value", {}) if isinstance(raw, dict) else {}
            if params.get("IsPlayer", {}).get("value", False):
                continue
            instance = str(entry["key"]["InstanceId"]["value"])
            owner = str(params.get("OwnerPlayerUId", {}).get("value", ""))
            old_owners = tuple(str(value) for value in params.get("OldOwnerPlayerUIds", {}).get("value", {}).get("values", []))
            actual_pal_owners[instance] = (owner, old_owners)
        if actual_pal_owners != expected_pal_owners:
            raise ValueError("Verifica post-scrittura: riferimenti proprietario dei Pal non coerenti.")
        actual_container_slots = []
        for container in vwsd.get("CharacterContainerSaveData", {}).get("value", []):
            for slot in container["value"].get("Slots", {}).get("value", {}).get("values", []):
                slot_raw = slot.get("RawData", {}).get("value", {})
                if isinstance(slot_raw, dict) and "player_uid" in slot_raw:
                    actual_container_slots.append((str(slot_raw["player_uid"]), str(slot_raw.get("instance_id", ""))))
        if actual_container_slots != original_container_slots:
            raise ValueError("Verifica post-scrittura: i collegamenti del Palbox/party sono cambiati.")
        output_groups = vwsd.get("GroupSaveDataMap", {}).get("value", [])
        for group_index, offset, expected_handle in protected_guild_handles:
            group_raw = output_groups[group_index]["value"].get("RawData", {}).get("value", {})
            group_blob = bytes(group_raw.get("values", []))
            if group_blob[offset:offset + 16] != expected_handle:
                raise ValueError("Verifica post-scrittura: un handle Pal della gilda non è stato preservato.")
    return f"Scambio verificato tra {old_guid} e {new_guid}."


@dataclass
class Player:
    player_id: str
    file: Path
    size: int
    virtual_name: str = ""
    actual_name: str = ""


class SaveEditor(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Palworld Save Browser feat Gobbi 90% code of chatgpt")
        
        self.setWindowIcon(QIcon(str(RESOURCE_DIR / "icon.png")))
        self.resize(940, 620)
        self.world_path: Path | None = None
        self.path_last_save: Path | None = None
        self.players: list[Player] = []
        self.names = self._load_names()
        self._build_ui()
        self._style()
        self._set_status("Seleziona la cartella di un mondo Palworld per iniziare.")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        top = QHBoxLayout()
        self.path_label = QLabel("Nessun mondo selezionato")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.browse_button = QPushButton("Apri cartella mondo…")
        self.browse_button.clicked.connect(self.choose_world)
        top.addWidget(self.path_label, 1)
        top.addWidget(self.browse_button)
        layout.addLayout(top)

        self.files_label = QLabel("File del mondo")
        self.files_table = QTableWidget(0, 3)
        self.files_table.setHorizontalHeaderLabels(["File", "Dimensione", "Formato"])
        self.files_table.horizontalHeader().setStretchLastSection(True)
        self.files_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.files_label)
        layout.addWidget(self.files_table, 1)

        self.players_label = QLabel("Giocatori")
        self.players_table = QTableWidget(0, 4)
        self.players_table.setHorizontalHeaderLabels(["GUID", "Nome virtuale", "Ruolo", "Dimensione"])
        self.players_table.horizontalHeader().setStretchLastSection(True)
        self.players_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.players_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.players_table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.players_label)
        layout.addWidget(self.players_table, 2)

        rename_row = QHBoxLayout()
        rename_row.addWidget(QLabel("Nome virtuale per il giocatore selezionato:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("es. Marco")
        self.save_name_button = QPushButton("Salva nome")
        self.save_name_button.clicked.connect(self.save_virtual_name)
        rename_row.addWidget(self.name_input, 1)
        rename_row.addWidget(self.save_name_button)
        layout.addLayout(rename_row)

        options = QHBoxLayout()
        self.backup_check = QCheckBox("S.backup")
        self.backup_check.setChecked(False)
        self.backup_check.setToolTip("Se selezionato, copia anche la cartella backup del mondo.")
        options.addWidget(self.backup_check)
        #options.addStretch(1)

        self.switch_button = QPushButton("Switch host")
        self.switch_button.clicked.connect(self.switch_host)
        self.switch_button.setToolTip("Scambia l'host col client scelto e crea il mondo modificato in SAVEEDITED.")
        self.SAVEEDITED_button = QPushButton("📂")
        self.SAVEEDITED_button.clicked.connect(lambda: self.open_explorer(OUTPUT_ROOT) )

        self.path_last_save_label = QLabel("Save attivo: non selezionato")
        self.path_last_save_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_last_save_label.setToolTip("Percorso del mondo installato: verrà usato per il ripristino del vecchio host come client.")
        self.path_last_save_label.setMaximumWidth(600)
        self.path_last_save_label.setWordWrap(True)
        self.inject_button = QPushButton("Inietta salvataggio")
        self.inject_button.clicked.connect(self.inject_host)
        self.inject_button.setToolTip(f"Carica il savegame appena editato in {SAVE_ROOT}.")
        self.restore_host_button = QPushButton("Metti dati vecchio host a client")
        self.restore_host_button.clicked.connect(self.restore_old_host_as_client)
        self.restore_host_button.setToolTip("Nel save indicato dalla label, sposta i dati conservati del vecchio host sulla GUID temporanea del nuovo client.")
        
        options.addWidget(self.path_last_save_label, 1)
        options.addWidget(self.switch_button)
        options.addWidget(self.SAVEEDITED_button)
        options.addWidget(self.inject_button)
        options.addWidget(self.restore_host_button)
        layout.addLayout(options)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
    def open_explorer(self,pathOpen):
        """Apre la cartella nel file system (estrae la cartella se il path punta a un file)."""
        import subprocess
        import platform

        path = pathOpen
        if not os.path.exists(path):
            return

        # Se il percorso punta a un file, prende la cartella in cui si trova
        if os.path.isfile(path):
            path = os.path.dirname(path)

        if platform.system() == "Windows":
            os.startfile(path)
        # elif platform.system() == "Darwin":  # macOS
        #     subprocess.run(["open", path])
        # else:  # Linux
        #     subprocess.run(["xdg-open", path])
    def _style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #17191d; color: #e7e9ed; font-size: 10pt; }
            QLabel { color: #c8ccd3; }
            QLineEdit, QTableWidget { background: #202329; border: 1px solid #3a3f48; border-radius: 5px; }
            QHeaderView::section { background: #292d34; color: #e7e9ed; padding: 6px; border: 0; }
            QPushButton { background: #343a44; border: 1px solid #4b5360; border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover:enabled { background: #414956; }
            QPushButton:disabled { color: #868b94; background: #24272c; }
            QCheckBox { spacing: 7px; }
        """)

    def _load_names(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("players", {}), dict):
                return data["players"]
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save_names(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps({"version": 1, "players": self.names}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def choose_world(self) -> None:
        start = str(self.world_path or SAVE_ROOT)
        chosen = QFileDialog.getExistingDirectory(self, "Seleziona cartella mondo", start)
        if chosen:
            self.load_world(Path(chosen))

    def load_world(self, path: Path) -> None:
        if not path.is_dir():
            QMessageBox.warning(self, "Cartella non valida", "La cartella selezionata non esiste.")
            return
        self.world_path = path
        self.path_label.setText(str(path))
        self._populate_world_files()
        self._populate_players()
        self._set_status("Lettura in sola consultazione. I file originali non vengono modificati.")

    def _populate_world_files(self) -> None:
        assert self.world_path
        files = sorted(p for p in self.world_path.iterdir() if p.is_file() and p.suffix.lower() == ".sav")
        self.files_table.setRowCount(len(files))
        for row, path in enumerate(files):
            try:
                header = path.read_bytes()[:128]
                fmt = "GVAS" if SAVE_MAGIC in header else "Non riconosciuto"
            except OSError:
                fmt = "Non leggibile"
            self.files_table.setItem(row, 0, QTableWidgetItem(path.name))
            self.files_table.setItem(row, 1, QTableWidgetItem(self._human_size(path.stat().st_size)))
            self.files_table.setItem(row, 2, QTableWidgetItem(fmt))
        self.files_table.resizeColumnsToContents()

    def _populate_players(self) -> None:
        assert self.world_path
        player_dir = self.world_path / "Players"
        paths = sorted(player_dir.glob("*.sav")) if player_dir.is_dir() else []
        self.players = []
        roster = {}
        if paths and (self.world_path / "Level.sav").is_file():
            try:
                roster = {p["guid"]: p for p in parse_player_roster(self.world_path)}
            except Exception as exc:
                self._set_status(f"Roster non decodificato: {exc}")
        for path in paths:
            player_id = path.stem.upper()
            scoped_key = f"{self.world_path.name}/{player_id}"
            entry = self.names.get(scoped_key, {})
            alias = entry.get("name", "") if isinstance(entry, dict) else ""
            actual = roster.get(player_id, {})
            game_name = actual.get("name", "")
            level = actual.get("level", "?")
            self.players.append(Player(player_id, path, path.stat().st_size, alias, game_name))
        self.players_table.setRowCount(len(self.players))
        for row, player in enumerate(self.players):
            actual = roster.get(player.player_id, {})
            role = "Host attuale" if player.player_id == HOST_ID else "Giocatore"
            role = f"{role} · {actual.get('name', 'nome ignoto')} · Lv {actual.get('level', '?')}"
            actual_name = actual.get("name", "")
            shown_name = " — ".join(x for x in (player.virtual_name, actual_name) if x) or "—"
            values = (player.player_id, shown_name, role, self._human_size(player.size))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, player.player_id)
                self.players_table.setItem(row, col, item)
        self.players_table.resizeColumnsToContents()
        self.name_input.clear()

    def _selected_player(self) -> Player | None:
        rows = self.players_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.players[rows[0].row()]

    def _selection_changed(self) -> None:
        player = self._selected_player()
        self.name_input.setText(player.virtual_name if player else "")

    def save_virtual_name(self) -> None:
        player = self._selected_player()
        if not player or not self.world_path:
            QMessageBox.information(self, "Seleziona un giocatore", "Seleziona una riga nella tabella giocatori.")
            return
        scoped_key = f"{self.world_path.name}/{player.player_id}"
        name = self.name_input.text().strip()
        if name:
            self.names[scoped_key] = {"name": name}
        else:
            self.names.pop(scoped_key, None)
        try:
            self._save_names()
        except OSError as exc:
            QMessageBox.critical(self, "Errore", f"Impossibile salvare il JSON dei nomi:\n{exc}")
            return
        self._populate_players()
        self._set_status(f"Nome virtuale salvato in {CONFIG_PATH.name}; nessun file .sav è stato cambiato.")

    def copy_world(self) -> None:
        if not self.world_path:
            return
        destination = OUTPUT_ROOT / self.world_path.name
        if destination.resolve() == self.world_path.resolve():
            QMessageBox.warning(self, "Destinazione non valida", "La cartella di output coincide con l'origine.")
            return
        if destination.exists():
            answer = QMessageBox.question(
                self,
                "Sostituire la copia?",
                f"{destination} esiste già. Vuoi sostituire questa cartella generata dall'app?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                shutil.rmtree(destination)
            except OSError as exc:
                QMessageBox.critical(self, "Errore", f"Impossibile sostituire la copia:\n{exc}")
                return
        ignored = shutil.ignore_patterns("backup") if not self.backup_check.isChecked() else None
        try:
            shutil.copytree(self.world_path, destination, ignore=ignored)
        except OSError as exc:
            QMessageBox.critical(self, "Errore", f"Copia non completata:\n{exc}")
            return
        QMessageBox.information(self, "Copia creata", f"Copia creata in:\n{destination}")
        self._set_status(f"Copia completa creata in {destination}; i dati dei .sav sono stati copiati senza modifiche.")
    def inject_host(self) -> None:
        '''Inietto il save editato nella cartella save pal world'''
        #inietto il save nella cartella di pal world usando path self.world_path ? verificare che è in appdata sennò aprire seezione cartella e uso SAVE_ROOT
        def is_within(path: Path, parent: Path) -> bool:
            try:
                path.resolve().relative_to(parent.resolve())
                return True
            except ValueError:
                return False

        def is_world(path: Path) -> bool:
            return path.is_dir() and (path / "Level.sav").is_file() and (path / "Players").is_dir()

        if not self.world_path:
            QMessageBox.information(self, "Seleziona un mondo", "Prima seleziona o carica la cartella del mondo da aggiornare.")
            return

        # Reuse the selected folder only when it is actually inside Palworld's
        # LOCALAPPDATA SaveGames tree. Otherwise ask for the destination world.
        target = self.world_path.resolve() if is_within(self.world_path, SAVE_ROOT) and is_world(self.world_path) else None
        if target is None:
            chosen = QFileDialog.getExistingDirectory(
                self,
                "Seleziona la cartella mondo di Palworld (dentro SaveGames)",
                str(SAVE_ROOT),
            )
            if not chosen:
                return
            target = Path(chosen).resolve()
            if not is_within(target, SAVE_ROOT):
                QMessageBox.warning(self, "Percorso fuori da Palworld", f"Seleziona una cartella dentro:\n{SAVE_ROOT}")
                return
            if not is_world(target):
                QMessageBox.warning(
                    self,
                    "Cartella mondo non valida",
                    "La cartella selezionata deve contenere Level.sav e Players. Seleziona la cartella del mondo, non la cartella SaveGames o SteamID.",
                )
                return

        # Prefer an explicitly loaded SAVEEDITED world. Otherwise use the
        # generated copy with the same world-folder name.
        source = None
        if is_within(self.world_path, OUTPUT_ROOT) and is_world(self.world_path):
            source = self.world_path.resolve()
        for candidate in (OUTPUT_ROOT / target.name, OUTPUT_ROOT / self.world_path.name):
            if source is None and is_world(candidate):
                source = candidate.resolve()
                break
        if source is None:
            chosen = QFileDialog.getExistingDirectory(
                self,
                "Seleziona il salvataggio modificato da installare",
                str(OUTPUT_ROOT),
            )
            if not chosen:
                return
            source = Path(chosen).resolve()
            if not is_within(source, OUTPUT_ROOT) or not is_world(source):
                QMessageBox.warning(self, "Salvataggio modificato non valido", f"Seleziona un mondo valido dentro:\n{OUTPUT_ROOT}")
                return

        if source == target:
            QMessageBox.warning(self, "Stessa cartella", "La sorgente modificata e la destinazione Palworld coincidono. Scegli una copia in SAVEEDITED.")
            return

        backup = None
        if target.exists():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = BACKUP_ROOT / f"ORIGINALSAVE[{timestamp}][{target.name}]"
            confirm = QMessageBox.question(
                self,
                "Backup e installazione",
                f"Il mondo esistente verrà spostato in:\n{backup}\n\n"
                f"Poi installerò questa copia:\n{source}\n\nDestinazione:\n{target}\n\nProcedere?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        else:
            confirm = QMessageBox.question(
                self,
                "Installare il salvataggio",
                f"Installare questa copia modificata?\n\n{source}\n\nDestinazione:\n{target}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        staging = None
        moved_original = False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.parent / f".{target.name}.injecting-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            shutil.copytree(source, staging)
            if target.exists():
                BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(backup))
                moved_original = True
            staging.replace(target)
            staging = None
        except Exception as exc:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if moved_original and backup is not None and backup.exists() and not target.exists():
                try:
                    shutil.move(str(backup), str(target))
                except OSError as restore_exc:
                    QMessageBox.critical(
                        self,
                        "Errore durante il ripristino",
                        f"Installazione non riuscita: {exc}\n\nAnche il ripristino automatico non è riuscito: {restore_exc}\n"
                        f"Il salvataggio originale si trova in:\n{backup}",
                    )
                    return
            QMessageBox.critical(self, "Installazione non riuscita", f"Non ho installato il nuovo salvataggio.\n\nDettaglio: {exc}")
            return

        message = f"Salvataggio installato in:\n{target}"
        if backup:
            message += f"\n\nOriginale conservato in:\n{backup}"
        self.path_last_save = target.resolve()
        self.path_last_save_label.setText(f"Save attivo: {self.path_last_save}")
        self.path_last_save_label.setToolTip(str(self.path_last_save))
        self.load_world(self.path_last_save)
        QMessageBox.information(self, "Installazione completata", message)
        self._set_status(message)

    @staticmethod
    def _is_valid_world(path: Path | None) -> bool:
        return bool(path and path.is_dir() and (path / "Level.sav").is_file() and (path / "Players").is_dir())

    def _choose_save_to_modify(self) -> Path | None:
        if self.path_last_save and self._is_valid_world(self.path_last_save):
            return self.path_last_save.resolve()
        chosen = QFileDialog.getExistingDirectory(self, "Seleziona il save di Palworld da modificare", str(SAVE_ROOT))
        if not chosen:
            return None
        selected = Path(chosen).resolve()
        try:
            selected.relative_to(SAVE_ROOT.resolve())
        except ValueError:
            QMessageBox.warning(self, "Percorso non valido", f"Scegli un mondo dentro:\n{SAVE_ROOT}")
            return None
        if not self._is_valid_world(selected):
            QMessageBox.warning(self, "Cartella non valida", "La cartella selezionata deve contenere Level.sav e Players.")
            return None
        self.path_last_save = selected
        self.path_last_save_label.setText(f"Save attivo: {selected}")
        self.path_last_save_label.setToolTip(str(selected))
        self.load_world(selected)
        return selected

    def _swap_profiles_in_active_save(self, first_guid: str, second_guid: str) -> str:
        target = self._choose_save_to_modify()
        if target is None:
            raise RuntimeError("Non è stato selezionato un save valido da modificare.")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        stage = target.parent / f".{target.name}.switching-{stamp}"
        backup = BACKUP_ROOT / f"ORIGINALSAVE[{stamp}][{target.name}]"
        moved_original = False
        try:
            swap_player_profiles(target, stage, first_guid, second_guid)
            BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup))
            moved_original = True
            stage.replace(target)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            if moved_original and backup.exists() and not target.exists():
                shutil.move(str(backup), str(target))
            raise
        self.path_last_save = target.resolve()
        self.path_last_save_label.setText(f"Save attivo: {self.path_last_save}")
        self.path_last_save_label.setToolTip(str(self.path_last_save))
        self.load_world(self.path_last_save)
        return f"Save modificato e verificato. Originale conservato in:\n{backup}"

    def switch_host(self) -> None:
        if not self.world_path:
            return
        clients = [p for p in self.players if p.player_id != HOST_ID]
        if not clients:
            QMessageBox.information(self, "Nessun client", "Questo mondo non ha giocatori client da promuovere a host.")
            return
        choices = [f"{p.virtual_name or p.actual_name or 'ERRORE'} — {p.player_id}" for p in clients]
        selected, ok = QInputDialog.getItem(self, "Nuovo host", "Scegli il player client da promuovere:", choices, 0, False)
        if not ok:
            return
        player = clients[choices.index(selected)]
        confirm = QMessageBox.question(
            self,
            "Conferma switch host",
            f"Promuovere {player.virtual_name or player.player_id} allo slot host 000…001?\n\n"
            f"Verrà creata una copia modificata in:\n{OUTPUT_ROOT / self.world_path.name}\n"
            "L'originale resterà invariato. Dopo aver iniettato la copia con ‘Inietta salvataggio’, il percorso installato apparirà nella label.\n"
            "Dopo lo switch, il vecchio host può entrare, creare un personaggio temporaneo dopo l'host chiude il server andando nel menu principale.\n"
            "Poi usa ‘Metti dati vecchio host a client’ e poi rihosta.\n"
            "Procedere?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        output = OUTPUT_ROOT / self.world_path.name
        if output.exists():
            answer = QMessageBox.question(
                self, "Sostituire la copia?", f"{output} esiste già. Sostituirla?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                shutil.rmtree(output)
            except OSError as exc:
                QMessageBox.critical(self, "Errore", f"Impossibile sostituire l'output:\n{exc}")
                return
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            message = swap_player_profiles(self.world_path, output, HOST_ID, player.player_id)
        except Exception as exc:
            QMessageBox.critical(self, "Switch annullato", f"Non è stato creato uno switch verificato.\n\nDettaglio: {exc}")
            if output.exists():
                shutil.rmtree(output, ignore_errors=True)
            return
        QMessageBox.information(self, "Switch completato", message + f"\n\nOutput:\n{output}")
        self._set_status(message + f" Output: {output}")

    def restore_old_host_as_client(self) -> None:
        target = self._choose_save_to_modify()
        if target is None:
            return
        try:
            current_players = parse_player_roster(target)
        except Exception as exc:
            QMessageBox.critical(self, "Errore lettura save", f"Non riesco a leggere i giocatori del save selezionato:\n{exc}")
            return
        clients = [p for p in current_players if p["guid"] != HOST_ID]
        if len(clients) < 2:
            QMessageBox.information(self, "Player insufficienti", "Servono il profilo conservato del vecchio host e il nuovo personaggio temporaneo.")
            return
        choices = []
        for item in clients:
            alias = self.names.get(f"{target.name}/{item['guid']}", {}).get("name", "")
            choices.append(f"{alias or item['name']} · Lv {item['level']} — {item['guid']}")
        source_choice, ok = QInputDialog.getItem(self, "Dati del vecchio host", "Seleziona il file che ora contiene i dati originali del vecchio host:", choices, 0, False)
        if not ok:
            return
        source_player = clients[choices.index(source_choice)]
        target_choices = [label for label, p in zip(choices, clients) ]#if p["guid"] != source_player["guid"]]
        target_choice, ok = QInputDialog.getItem(self, "Nuova GUID client", "Seleziona il personaggio temporaneo creato dal vecchio host dopo essere rientrato:", target_choices, 0, False)
        if not ok:
            return
        target_player = clients[[label for label, p in zip(choices, clients) ].index(target_choice)]#if p["guid"] != source_player["guid"]
        confirm = QMessageBox.question(
            self,
            "Conferma ripristino",
            f"Scambiare i dati di {source_player['name']} con lo slot temporaneo {target_player['guid']}?\n\n"
            f"Save da modificare:\n{target}\n\n"
            "Lo slot host 000…001 non verrà toccato. Il save attivo verrà aggiornato direttamente dopo averne creato un backup.\n"
            "Assicurati di aver chiuso Palworld e di aver selezionato le due GUID corrette.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            message = self._swap_profiles_in_active_save(source_player["guid"], target_player["guid"])
        except Exception as exc:
            QMessageBox.critical(self, "Ripristino annullato", f"Il save non è stato aggiornato.\n\nDettaglio: {exc}")
            return
        QMessageBox.information(self, "Ripristino completato", message + f"\n\nPercorso modificato:\n{self.path_last_save}")
        self._set_status(message)

    def _set_status(self, message: str) -> None:
        self.status.setText(message)

    @staticmethod
    def _human_size(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.2f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"


def main() -> int:
    app = QApplication(sys.argv)
    window = SaveEditor()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
