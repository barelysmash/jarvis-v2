const { app, BrowserWindow, screen } = require("electron");
const path = require("path");
const fs = require("fs");

// `npm run electron`            -> HUD window (unchanged)
// `npm run electron:cockpit`    -> HUD + cockpit on the second display
// `npm run electron:cockpit-only` -> cockpit only (e.g. a dedicated screen)
//
// The cockpit always loads from the JARVIS server (same origin as its
// /ws/cockpit socket). Override with JARVIS_URL, e.g.
//   JARVIS_URL=http://guildenstern:8765 npm run electron:cockpit
const JARVIS_URL = process.env.JARVIS_URL || "http://100.113.110.44:8765";
const wantCockpit = process.argv.includes("--cockpit") || process.argv.includes("--cockpit-only");
const cockpitOnly = process.argv.includes("--cockpit-only");

function createHudWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 760,
    minWidth: 1180,
    minHeight: 700,
    frame: true,
    backgroundColor: "#000204",
    autoHideMenuBar: true,
    title: "JARVIS",
    webPreferences: { contextIsolation: true },
  });
  const distPath = path.join(__dirname, "dist/index.html");
  const useDevServer =
    process.env.NODE_ENV === "development" || !fs.existsSync(distPath);
  if (useDevServer) win.loadURL("http://localhost:5173");
  else win.loadFile(distPath);
}

function createCockpitWindow() {
  const displays = screen.getAllDisplays();
  const primary = screen.getPrimaryDisplay();
  const target = displays.find((d) => d.id !== primary.id) || primary;
  const { x, y, width, height } = target.workArea;
  const win = new BrowserWindow({
    x,
    y,
    width,
    height,
    backgroundColor: "#02080c",
    autoHideMenuBar: true,
    title: "JARVIS · Cockpit",
    webPreferences: { contextIsolation: true },
  });
  if (target.id !== primary.id) win.maximize();
  const devCockpit = process.env.NODE_ENV === "development";
  win.loadURL(
    devCockpit ? "http://localhost:5173/cockpit.html" : `${JARVIS_URL}/cockpit.html`
  );
}

app.whenReady().then(() => {
  if (!cockpitOnly) createHudWindow();
  if (wantCockpit) createCockpitWindow();
});
app.on("window-all-closed", () => app.quit());
