# Kapacitet+ in your browser

You can use Kapacitet+ without installing anything. It runs in a *codespace*: a computer in
GitHub's cloud that only you can open, shown in your browser. A free GitHub account includes 60
hours of it a month.

## What you need

- A GitHub account (free).
- Chrome or Edge.

The data come by themselves: the codespace downloads them from the project's
[data release](https://github.com/akamaxe/kapacitet-plus/releases/tag/data).

## The first time (about ten minutes)

1. Open this link, and save it as a bookmark:
   **<https://codespaces.new/akamaxe/kapacitet-plus?quickstart=1>**
2. Sign in to GitHub if it asks, then click **Create new codespace**.
3. Wait. A page that looks like a code editor appears and sets itself up for a few minutes. You do
   not need to type anything in it.
4. If it asks whether you trust the authors of the files in this folder, choose
   **Trust Folder and Continue**. Kapacitet+ cannot start otherwise.
5. Kapacitet+ opens in a new tab, with its screens.

## The next times

Open the same link and click **Resume**. Your copy opens again in about a minute, as you left it.

## While you use it

- **Keep the editor tab open**, even in the background. Kapacitet+ writes a line there for
  everything you do, and that is what keeps the codespace running.
- When you close both tabs, the codespace stops by itself after 30 minutes and stops using your free
  hours. To stop it at once, go to <https://github.com/codespaces>, open the **…** menu next to it and
  choose **Stop codespace**.
- **Do not make the port public.** Kapacitet+ has no login, so anyone with the link could use your
  copy. It is private unless you change it.

## If something goes wrong

| What you see | What to do |
|---|---|
| No new tab opens | In the editor tab, click **Ports** at the bottom, then the globe next to **Kapacitet+ (8000)**. If the browser blocked a pop-up, allow pop-ups for github.dev. |
| Kapacitet+ asks you to **Load the data** | The download did not work. Download `kapacitet-data.zip` from the [data release](https://github.com/akamaxe/kapacitet-plus/releases/tag/data), click **Choose kapacitet-data.zip** and pick it. It uploads and unpacks in a few minutes, then Kapacitet+ restarts by itself. |
| A folder instead of a `.zip` file (Safari on a Mac unpacks what it downloads) | Right-click the folder and choose **Compress**, then load the `.zip` it makes. Or download the file again with Chrome. |
| "The upload stopped" | Choose the same file again: the upload carries on where it stopped. |
| The codespace stops while you are still reading | Raise the idle limit, up to 4 hours: your picture on GitHub, **Settings**, **Codespaces**, **Default idle timeout**. |
| Your codespace is gone | GitHub deletes a codespace that has been stopped for 30 days. Open the link and create a new one. |
| A newer version of Kapacitet+ or of the data | Delete the codespace at <https://github.com/codespaces>, then open the link again. |

## For whoever maintains it

- The codespace is defined in `.devcontainer/`. `install.sh` builds the engine and the interface
  when the codespace is created; `data.sh` downloads the data release and unpacks it; `start.sh`
  starts the server each time someone opens the codespace and keeps the server's log in a terminal,
  which is what counts as activity for GitHub's idle timeout.
- The data are never committed. They are published as the asset of the release `data`, under ODbL
  (`DATA-LICENSE.md`). After a new compute:
  `python -m kapacitet bundle --out ../kapacitet-data.zip`, then
  `gh release upload data ../kapacitet-data.zip --clobber`.
- The setup page's upload (`api/setup.py`) is open only when the server allows it
  (`KAPACITET_SETUP_UPLOAD=1`, set in `devcontainer.json`), on the reference network, and while the
  copy has no results. A codespace's port is private to whoever created it.
