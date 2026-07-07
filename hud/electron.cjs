const { app, BrowserWindow } = require("electron");
const path = require("path");
const fs = require("fs");

function createWindow() {
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

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
