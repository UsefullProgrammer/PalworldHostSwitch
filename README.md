# Palworld Save Browser

Small desktop utility for browsing local Palworld save folders, keeping
your own labels for player GUIDs, and making a separate copy of a world.


## Run

Requires:

palworld-save-tools==0.24.0
pyooz

main_without_pyqty6.py: Python 3.10+ 
python main_legacy.py: Python 3.10+ and PyQt6: Is legacy edition, not use it i not update was nice graphic but not very necessary


```powershell
windows:
winget install Python.Python.3.11
pip install -r requirements.txt
python PalworldHostSwitch.py
linux:
sudo apt update && sudo apt install python3 python3-pip
pip3 install -r requirements.txt
python PalworldHostSwitch.py
```
Get Folder savegame by host and rename with -2 or (1) example 5DCCEC12441C00319DF7B2BD522272CE in 5DCCEC12441C00319DF7B2BD522272CE(1)
Select the folder savegame containing `Level.sav`, `LevelMeta.sav`, `LocalData.sav`, and
`Players`. The app lists the top-level `.sav` files and player files, and reads
player names and levels from the world registry.

Virtual names are stored separately in `player_names.json`, keyed by world
folder and player GUID. They do not rename characters in the game and are not
written into any `.sav` file.

“Create copy in SAVEEDITED” copies the selected world folder to `SAVEEDITED/<world
folder name>`. Backups are excluded by default and can be included using the
checkbox. If that output folder already exists, the app asks before replacing
it. The source save folder is never modified.

Inject savegame button upload savegame in your folder savegame of game.

Run game, join in savegame (ignore name of player)

Left and see that now have just name.

Old Host need: join server be new character after leave, New host close server (return to menù principale ndr da traddure) and click on "metti dati vecchio host a client" ndr da traddure select old host, and after select empyty character, and is done, after rehost.  

## Switch host

The app includes a PlM/Oodle-aware parser from the open-source Palworld Save
Toolkit under `palworld-save-toolkit/`. “Switch host” exchanges the current host
(`...0001`) with an existing client in a copied world. Player files and world
references are updated, then the result is reparsed and checked. Co-op palbox
slot references remain attached to the special host GUID, pal ownership is
reassigned between the two player GUIDs, and pal handles are preserved while
player references in guild data are exchanged. GUID byte order follows the
serialized Unreal `FGuid` layout.

To restore the old host as a client, first load that output world and have the
old host join once and create a temporary character. Close the game, select the
resulting world folder in this app, then click “Metti dati vecchio host a
client”. Choose the GUID file holding the old host's original character and the
new temporary character GUID. The second operation exchanges those two client
profiles, leaving the new host in `...0001`, and writes a separate
`SAVEEDITED/<world>_CLIENT` copy. Both operations leave their source folder
untouched and abort/remove partial output if validation fails. Use “Inietta
salvataggio” to install a selected `SAVEEDITED` copy into the active game save.

## Code and dependency review

The toolkit was cloned from `zlmitchell/palworld-save-toolkit` at commit
`e71370ede118311e1a2b354caadb6d527ca9bcd2` and its GPL-3.0 license is present.
Static inspection of the Python parser/migration modules imported by this app
found no network calls, shell execution, subprocesses, or obfuscated payloads.
The browser demo and local web server in that repository are not used by this
app. This is a source review, not a formal security guarantee; Python, PyQt6,
and the native `pyooz` dependency remain third-party software.

`palworld-save-toolkit/` is licensed GPL-3.0. Dependencies are PyQt6,
`palworld-save-tools==0.24.0`, and `pyooz`.


## NOTE
    Level.sav not is more supported but not is relevant.
