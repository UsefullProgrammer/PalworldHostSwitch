"""Palworld Save Browser and Host Switch workspace.

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

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if getattr(sys, 'frozen', False):
    # Se è un eseguibile, la cartella di base è quella temporanea di PyInstaller
    # (assicurati di includere la cartella nel comando di build)
    BASE_DIR = Path(os.path.dirname(sys.executable))
    #BASE_DIR = Path(sys._MEIPASS)
else:
    # Se è uno script normale
    BASE_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parent.parent
SAVE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Pal" / "Saved" / "SaveGames"
#SAVE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Pal" / "Saved" / "SaveGames"
OUTPUT_ROOT = ROOT / "SAVEEDITED"
BACKUP_ROOT = ROOT / "backupSAVE"
HOST_ID = "00000000000000000000000000000001"
CONFIG_PATH = Path(__file__).resolve().parent / "player_names.json"
SAVE_MAGIC = b"GVAS"
TOOLKIT_PY = BASE_DIR / "palworld-save-toolkit" / "python"
VENDOR_PY = TOOLKIT_PY / "vendor"


def load_toolkit():
    #TODO da fixare perchè in release non funziona il path
    """Import the toolkit's PlM/Oodle-aware parser ahead of any installed copy."""
    for path in (str(TOOLKIT_PY), str(VENDOR_PY)):
        if path not in sys.path:
            sys.path.insert(0, path)
    from palworld_save_tools.archive import UUID
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
    from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    return UUID, GvasFile, compress_gvas_to_sav, decompress_sav_to_gvas, PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS


def parse_player_roster(world: Path) -> list[dict[str, str]]:
    """Read player file IDs and names/levels from the world's Level.sav."""
    #100% gpt non ho idea di che minchia faccia.
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
    #revisionata da Gobbi
    normalized = guid32.strip().replace("-", "").upper()
    if not re.fullmatch(r"[0-9A-F]{32}", normalized):
        raise ValueError("La GUID deve contenere esattamente 32 caratteri esadecimali.")
    dashed = f"{normalized[:8]}-{normalized[8:12]}-{normalized[12:16]}-{normalized[16:20]}-{normalized[20:]}".lower()
    # Palworld's serialized FGuid byte order is UUID.bytes_le, not hex-string order.
    return dashed, uuid.UUID(normalized).bytes_le


def swap_player_profiles(source: Path, output: Path, first_guid: str, second_guid: str) -> str:
    """Exchange two player identities and their world references into a new copy."""
    #TODO da fixare perchèsembrerebbe non switchi alcuni parametri della base
    #99% gpt
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


class SaveEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Palworld Save Browser feat Gobbi 90% code of chatgpt")
        self.geometry("1100x760")
        self.minsize(860, 620)
        self.configure(bg="#17191d")
        try:
            self.iconphoto(True, tk.PhotoImage(file=str(Path(__file__).resolve().parent / "icon.png")))
        except (tk.TclError, OSError):
            pass
        self.world_path: Path | None = None
        self.path_last_save: Path | None = None
        self.players: list[Player] = []
        self.names = self._load_names()
        self._build_ui()
        #self._style()
        self._set_status("Seleziona la cartella di un mondo Palworld per iniziare.")
    
    def _build_ui(self) -> None:
         #revisionata da Gobbi
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#17191d")
        style.configure("TLabel", background="#17191d", foreground="#c8ccd3", font=("Segoe UI", 10))
        style.configure("TButton", background="#343a44", foreground="#e7e9ed", padding=(10, 6))
        style.map("TButton", background=[("active", "#414956")])
        style.configure("Treeview", background="#202329", fieldbackground="#202329", foreground="#e7e9ed", rowheight=25)
        style.configure("Treeview.Heading", background="#292d34", foreground="#e7e9ed", padding=6)
        style.map("Treeview", background=[("selected", "#465568")], foreground=[("selected", "#ffffff")])
        style.configure("TEntry", fieldbackground="#202329", foreground="#e7e9ed", padding=5)

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        root.rowconfigure(4, weight=2)

        top = ttk.Frame(root)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(0, weight=1)
        self.path_label = ttk.Label(top, text="Nessun mondo selezionato")
        self.path_label.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Button(top, text="Apri cartella mondo…", command=self.choose_world).grid(row=0, column=1)

        ttk.Label(root, text="File del mondo").grid(row=1, column=0, sticky="w")
        self.files_table = ttk.Treeview(root, columns=("file", "size", "format"), show="headings", selectmode="browse", height=7)
        for col, title, width in (("file", "File", 450), ("size", "Dimensione", 130), ("format", "Formato", 180)):
            self.files_table.heading(col, text=title)
            self.files_table.column(col, width=width, anchor="w")
        self.files_table.grid(row=2, column=0, sticky="nsew", pady=(4, 10))
        files_scroll = ttk.Scrollbar(root, orient="vertical", command=self.files_table.yview)
        files_scroll.grid(row=2, column=1, sticky="ns", pady=(4, 10))
        self.files_table.configure(yscrollcommand=files_scroll.set)

        ttk.Label(root, text="Giocatori").grid(row=3, column=0, sticky="w")
        self.players_table = ttk.Treeview(root, columns=("guid", "name", "role", "size"), show="headings", selectmode="browse", height=9)
        for col, title, width in (("guid", "GUID", 300), ("name", "Nome virtuale", 190), ("role", "Ruolo", 390), ("size", "Dimensione", 120)):
            self.players_table.heading(col, text=title)
            self.players_table.column(col, width=width, anchor="w")
        self.players_table.grid(row=4, column=0, sticky="nsew", pady=(4, 10))
        players_scroll = ttk.Scrollbar(root, orient="vertical", command=self.players_table.yview)
        players_scroll.grid(row=4, column=1, sticky="ns", pady=(4, 10))
        self.players_table.configure(yscrollcommand=players_scroll.set)
        self.players_table.bind("<<TreeviewSelect>>", lambda _event: self._selection_changed())

        rename = ttk.Frame(root)
        rename.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        rename.columnconfigure(1, weight=1)
        ttk.Label(rename, text="Nome virtuale per il giocatore selezionato:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.name_input = ttk.Entry(rename)
        self.name_input.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(rename, text="Salva nome", command=self.save_virtual_name).grid(row=0, column=2)

        options = ttk.Frame(root)
        options.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        options.columnconfigure(1, weight=1)
        self.backup_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="S.backup", variable=self.backup_var).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.path_last_save_label = ttk.Label(options, text="Save attivo: non selezionato", wraplength=420)
        self.path_last_save_label.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Button(options, text="Switch host", command=self.switch_host).grid(row=0, column=2, padx=2)
        ttk.Button(options, text="📂", command=lambda: self.open_explorer(OUTPUT_ROOT)).grid(row=0, column=3, padx=2)
        ttk.Button(options, text="Inietta salvataggio", command=self.inject_host).grid(row=0, column=4, padx=2)
        ttk.Button(options, text="Metti dati vecchio host a client", command=self.restore_old_host_as_client).grid(row=0, column=5, padx=2)
        self.status = ttk.Label(root, text="", wraplength=1050)
        self.status.grid(row=7, column=0, sticky="ew")
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
        chosen = filedialog.askdirectory(parent=self, title="Seleziona cartella mondo", initialdir=start)
        if chosen:
            self.load_world(Path(chosen))

    def load_world(self, path: Path) -> None:
        if not path.is_dir():
            messagebox.showwarning("Cartella non valida", "La cartella selezionata non esiste.", parent=self)
            return
        self.world_path = path
        self.path_label.configure(text=str(path))
        self._populate_world_files()
        self._populate_players()
        self._set_status("Lettura in sola consultazione. I file originali non vengono modificati.")

    def _populate_world_files(self) -> None:
        assert self.world_path
        files = sorted(p for p in self.world_path.iterdir() if p.is_file() and p.suffix.lower() == ".sav")
        self.files_table.delete(*self.files_table.get_children())
        for row, path in enumerate(files):
            try:
                header = path.read_bytes()[:128]
                fmt = "GVAS" if SAVE_MAGIC in header else "Non riconosciuto"
            except OSError:
                fmt = "Non leggibile"
            self.files_table.insert("", "end", values=(path.name, self._human_size(path.stat().st_size), fmt))

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
        self.players_table.delete(*self.players_table.get_children())
        for row, player in enumerate(self.players):
            actual = roster.get(player.player_id, {})
            role = "Host attuale" if player.player_id == HOST_ID else "Giocatore"
            role = f"{role} · {actual.get('name', 'nome ignoto')} · Lv {actual.get('level', '?')}"
            actual_name = actual.get("name", "")
            shown_name = " — ".join(x for x in (player.virtual_name, actual_name) if x) or "—"
            values = (player.player_id, shown_name, role, self._human_size(player.size))
            self.players_table.insert("", "end", iid=player.player_id, values=values)
        self.name_input.delete(0, "end")

    def _selected_player(self) -> Player | None:
        selected = self.players_table.selection()
        if not selected:
            return None
        return next((player for player in self.players if player.player_id == selected[0]), None)

    def _selection_changed(self) -> None:
        player = self._selected_player()
        self.name_input.delete(0, "end")
        if player:
            self.name_input.insert(0, player.virtual_name)

    def _choose_item(self, title: str, prompt: str, choices: list[str]) -> str | None:
        """Show a small Tk dialog for selecting one item from a list."""
        if not choices:
            return None
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(bg="#17191d")
        dialog.geometry("620x170")
        result: list[str] = []
        ttk.Label(dialog, text=prompt, wraplength=580).pack(fill="x", padx=12, pady=(12, 6))
        selected = tk.StringVar(value=choices[0])
        combo = ttk.Combobox(dialog, textvariable=selected, values=choices, state="readonly")
        combo.pack(fill="x", padx=12, pady=6)
        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=12, pady=10)

        def accept() -> None:
            result.append(selected.get())
            dialog.destroy()

        ttk.Button(buttons, text="Annulla", command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Seleziona", command=accept).pack(side="right")
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        combo.focus_set()
        self.wait_window(dialog)
        return result[0] if result else None

    def save_virtual_name(self) -> None:
        player = self._selected_player()
        if not player or not self.world_path:
            messagebox.showinfo("Seleziona un giocatore", "Seleziona una riga nella tabella giocatori.", parent=self)
            return
        scoped_key = f"{self.world_path.name}/{player.player_id}"
        name = self.name_input.get().strip()
        if name:
            self.names[scoped_key] = {"name": name}
        else:
            self.names.pop(scoped_key, None)
        try:
            self._save_names()
        except OSError as exc:
            messagebox.showerror("Errore", f"Impossibile salvare il JSON dei nomi:\n{exc}", parent=self)
            return
        self._populate_players()
        self._set_status(f"Nome virtuale salvato in {CONFIG_PATH.name}; nessun file .sav è stato cambiato.")

    def copy_world(self) -> None:
        if not self.world_path:
            return
        destination = OUTPUT_ROOT / self.world_path.name
        if destination.resolve() == self.world_path.resolve():
            messagebox.showwarning("Destinazione non valida", "La cartella di output coincide con l'origine.", parent=self)
            return
        if destination.exists():
            answer = messagebox.askyesno("Sostituire la copia?", f"{destination} esiste già. Vuoi sostituire questa cartella generata dall'app?", parent=self)
            if not answer:
                return
            try:
                shutil.rmtree(destination)
            except OSError as exc:
                messagebox.showerror("Errore", f"Impossibile sostituire la copia:\n{exc}", parent=self)
                return
        ignored = shutil.ignore_patterns("backup") if not self.backup_var.get() else None
        try:
            shutil.copytree(self.world_path, destination, ignore=ignored)
        except OSError as exc:
            messagebox.showerror("Errore", f"Copia non completata:\n{exc}", parent=self)
            return
        messagebox.showinfo("Copia creata", f"Copia creata in:\n{destination}", parent=self)
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
            messagebox.showinfo("Seleziona un mondo", "Prima seleziona o carica la cartella del mondo da aggiornare.", parent=self)
            return

        # Reuse the selected folder only when it is actually inside Palworld's
        # LOCALAPPDATA SaveGames tree. Otherwise ask for the destination world.
        target = self.world_path.resolve() if is_within(self.world_path, SAVE_ROOT) and is_world(self.world_path) else None
        if target is None:
            chosen = filedialog.askdirectory(parent=self, title="Seleziona la cartella mondo di Palworld (dentro SaveGames)", initialdir=str(SAVE_ROOT))
            if not chosen:
                return
            target = Path(chosen).resolve()
            if not is_within(target, SAVE_ROOT):
                messagebox.showwarning("Percorso fuori da Palworld", f"Seleziona una cartella dentro:\n{SAVE_ROOT}", parent=self)
                return
            if not is_world(target):
                messagebox.showwarning("Cartella mondo non valida", "La cartella selezionata deve contenere Level.sav e Players. Seleziona la cartella del mondo, non la cartella SaveGames o SteamID.", parent=self)
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
            chosen = filedialog.askdirectory(parent=self, title="Seleziona il salvataggio modificato da installare", initialdir=str(OUTPUT_ROOT))
            if not chosen:
                return
            source = Path(chosen).resolve()
            if not is_within(source, OUTPUT_ROOT) or not is_world(source):
                messagebox.showwarning("Salvataggio modificato non valido", f"Seleziona un mondo valido dentro:\n{OUTPUT_ROOT}", parent=self)
                return

        if source == target:
            messagebox.showwarning("Stessa cartella", "La sorgente modificata e la destinazione Palworld coincidono. Scegli una copia in SAVEEDITED.", parent=self)
            return

        backup = None
        if target.exists():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = BACKUP_ROOT / f"ORIGINALSAVE[{timestamp}][{target.name}]"
            confirm = messagebox.askyesno("Backup e installazione", f"Il mondo esistente verrà spostato in:\n{backup}\n\nPoi installerò questa copia:\n{source}\n\nDestinazione:\n{target}\n\nProcedere?", parent=self)
            if not confirm:
                return
        else:
            confirm = messagebox.askyesno("Installare il salvataggio", f"Installare questa copia modificata?\n\n{source}\n\nDestinazione:\n{target}", parent=self)
            if not confirm:
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
                    messagebox.showerror("Errore durante il ripristino", f"Installazione non riuscita: {exc}\n\nAnche il ripristino automatico non è riuscito: {restore_exc}\nIl salvataggio originale si trova in:\n{backup}", parent=self)
                    return
            messagebox.showerror("Installazione non riuscita", f"Non ho installato il nuovo salvataggio.\n\nDettaglio: {exc}", parent=self)
            return

        message = f"Salvataggio installato in:\n{target}"
        if backup:
            message += f"\n\nOriginale conservato in:\n{backup}"
        self.path_last_save = target.resolve()
        self.path_last_save_label.configure(text=f"Save attivo: {self.path_last_save}")
        self.load_world(self.path_last_save)
        messagebox.showinfo("Installazione completata", message, parent=self)
        self._set_status(message)

    @staticmethod
    def _is_valid_world(path: Path | None) -> bool:
        return bool(path and path.is_dir() and (path / "Level.sav").is_file() and (path / "Players").is_dir())

    def _choose_save_to_modify(self) -> Path | None:
        if self.path_last_save and self._is_valid_world(self.path_last_save):
            return self.path_last_save.resolve()
        chosen = filedialog.askdirectory(parent=self, title="Seleziona il save di Palworld da modificare", initialdir=str(SAVE_ROOT))
        if not chosen:
            return None
        selected = Path(chosen).resolve()
        try:
            selected.relative_to(SAVE_ROOT.resolve())
        except ValueError:
            messagebox.showwarning("Percorso non valido", f"Scegli un mondo dentro:\n{SAVE_ROOT}", parent=self)
            return None
        if not self._is_valid_world(selected):
            messagebox.showwarning("Cartella non valida", "La cartella selezionata deve contenere Level.sav e Players.", parent=self)
            return None
        self.path_last_save = selected
        self.path_last_save_label.configure(text=f"Save attivo: {selected}")
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
        self.path_last_save_label.configure(text=f"Save attivo: {self.path_last_save}")
        self.load_world(self.path_last_save)
        return f"Save modificato e verificato. Originale conservato in:\n{backup}"

    def switch_host(self) -> None:
        if not self.world_path:
            return
        clients = [p for p in self.players if p.player_id != HOST_ID]
        if not clients:
            messagebox.showinfo("Nessun client", "Questo mondo non ha giocatori client da promuovere a host.", parent=self)
            return
        choices = [f"{p.virtual_name or p.actual_name or 'ERRORE'} — {p.player_id}" for p in clients]
        selected = self._choose_item("Nuovo host", "Scegli il player client da promuovere:", choices)
        if selected is None:
            return
        player = clients[choices.index(selected)]
        
        confirm = messagebox.askyesno("Conferma switch host",
            f"Promuovere {player.virtual_name or player.player_id} allo slot host 000…001?\n\n"
            f"Verrà creata una copia modificata in:\n{OUTPUT_ROOT / self.world_path.name}\n"
            "L'originale resterà invariato. Dopo aver iniettato la copia con ‘Inietta salvataggio’, il percorso installato apparirà nella label.\n"
            "Dopo lo switch, il vecchio host può entrare, creare un personaggio temporaneo dopo l'host chiude il server andando nel menu principale.\n"
            "Poi usa ‘Metti dati vecchio host a client’ e poi rihosta.\n"
            "Procedere?", parent=self)
        if not confirm:
            return
        output = OUTPUT_ROOT / self.world_path.name
        if output.exists():
            answer = messagebox.askyesno("Sostituire la copia?", f"{output} esiste già. Sostituirla?", parent=self)
            if not answer:
                return
            try:
                shutil.rmtree(output)
            except OSError as exc:
                messagebox.showerror("Errore", f"Impossibile sostituire l'output:\n{exc}", parent=self)
                return
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            message = swap_player_profiles(self.world_path, output, HOST_ID, player.player_id)
        except Exception as exc:
            messagebox.showerror("Switch annullato", f"Non è stato creato uno switch verificato.\n\nDettaglio: {exc}", parent=self)
            if output.exists():
                shutil.rmtree(output, ignore_errors=True)
            return
        messagebox.showinfo("Switch completato", message + f"\n\nOutput:\n{output}", parent=self)
        self._set_status(message + f" Output: {output}")

    def restore_old_host_as_client(self) -> None:
        target = self._choose_save_to_modify()
        if target is None:
            return
        try:
            current_players = parse_player_roster(target)
        except Exception as exc:
            messagebox.showerror("Errore lettura save", f"Non riesco a leggere i giocatori del save selezionato:\n{exc}", parent=self)
            return
        clients = [p for p in current_players if p["guid"] != HOST_ID]
        if len(clients) < 2:
            messagebox.showinfo("Player insufficienti", "Servono il profilo conservato del vecchio host e il nuovo personaggio temporaneo.", parent=self)
            return
        choices = []
        for item in clients:
            alias = self.names.get(f"{target.name}/{item['guid']}", {}).get("name", "")
            choices.append(f"{alias or item['name']} · Lv {item['level']} — {item['guid']}")
        source_choice = self._choose_item("Dati del vecchio host", "Seleziona il file che ora contiene i dati originali del vecchio host:", choices)
        if source_choice is None:
            return
        source_player = clients[choices.index(source_choice)]
        target_choices = [label for label, p in zip(choices, clients) ]#if p["guid"] != source_player["guid"]]
        target_choice = self._choose_item("Nuova GUID client", "Seleziona il personaggio temporaneo creato dal vecchio host dopo essere rientrato:", target_choices)
        if target_choice is None:
            return
        target_player = clients[[label for label, p in zip(choices, clients) ].index(target_choice)]#if p["guid"] != source_player["guid"]
        confirm = messagebox.askyesno("Conferma ripristino",
            f"Scambiare i dati di {source_player['name']} con lo slot temporaneo {target_player['guid']}?\n\n"
            f"Save da modificare:\n{target}\n\n"
            "Lo slot host 000…001 non verrà toccato. Il save attivo verrà aggiornato direttamente dopo averne creato un backup.\n"
            "Assicurati di aver chiuso Palworld e di aver selezionato le due GUID corrette.", parent=self)
        if not confirm:
            return
        try:
            message = self._swap_profiles_in_active_save(source_player["guid"], target_player["guid"])
        except Exception as exc:
            messagebox.showerror("Ripristino annullato", f"Il save non è stato aggiornato.\n\nDettaglio: {exc}", parent=self)
            return
        messagebox.showinfo("Ripristino completato", message + f"\n\nPercorso modificato:\n{self.path_last_save}", parent=self)
        self._set_status(message)

    def _set_status(self, message: str) -> None:
        self.status.configure(text=message)

    @staticmethod
    def _human_size(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.2f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"


def main() -> int:
    window = SaveEditor()
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
