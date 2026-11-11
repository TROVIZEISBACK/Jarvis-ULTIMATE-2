// JARVIS.app — native macOS shell for JARVIS Ultimate 2.
//
// Starts the Python core (run.sh) as a child process, so macOS attributes the
// Microphone, Accessibility, Screen Recording and Camera permissions to JARVIS.app,
// and shows the holographic interface in its own window (WKWebView).
// Build with ./make_app.sh.

import Cocoa
import WebKit
import UniformTypeIdentifiers

let info = Bundle.main.infoDictionary ?? [:]
let projectDir = info["JARVISProjectDir"] as? String ?? ""
let port = info["JARVISPort"] as? String ?? "8765"
let coreURL = URL(string: "http://127.0.0.1:\(port)/")!
let healthURL = URL(string: "http://127.0.0.1:\(port)/health")!
/// Health checks must reach the live server, never a cached answer.
let probeSession: URLSession = {
    let c = URLSessionConfiguration.ephemeral
    c.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
    c.urlCache = nil
    c.timeoutIntervalForRequest = 1
    return URLSession(configuration: c)
}()
/// The interface is always revalidated, so UI updates show up on the next load.
func coreRequest() -> URLRequest { URLRequest(url: coreURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10) }
let logPath = NSHomeDirectory() + "/Library/Logs/JARVIS.log"

/// One append-only log for the app and the core (both write with O_APPEND, so lines never overwrite).
func openLogFile() -> Int32 { open(logPath, O_WRONLY | O_APPEND | O_CREAT, 0o644) }
let logFD: Int32 = {
    if let size = (try? FileManager.default.attributesOfItem(atPath: logPath))?[.size] as? Int, size > 1_000_000 {
        try? FileManager.default.removeItem(atPath: logPath)
    }
    return openLogFile()
}()
let stampFormat: DateFormatter = { let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f }()
func log(_ msg: String) {
    let line = "[\(stampFormat.string(from: Date()))] app: \(msg)\n"
    _ = line.withCString { write(logFD, $0, strlen($0)) }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var core: Process?
    var quitting = false
    var waitStarted = Date()
    var coreStarted = Date()
    var lastAutoRestart = Date.distantPast
    var watchdog: Timer?

    // MARK: lifecycle
    var activity: NSObjectProtocol?

    func applicationDidFinishLaunching(_ note: Notification) {
        log("launched (pid \(getpid()))")
        // No App Nap: JARVIS must stay responsive in the background (⌘H, timers, the core).
        activity = ProcessInfo.processInfo.beginActivity(options: .userInitiatedAllowingIdleSystemSleep,
                                                         reason: "JARVIS assistant running")
        buildMenu()
        buildWindow()
        boot()
        NSApp.activate(ignoringOtherApps: true)
        // If we attached to a core we didn't start (./run.sh) and it goes away, start our own.
        watchdog = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { _ in
            guard self.core == nil, !self.quitting, !self.showingError else { return }
            self.probe { alive in
                if !alive && self.core == nil { log("watchdog: no core running, starting one"); self.boot() }
            }
        }
    }
    var showingError = false

    var booting = false
    func boot() {
        guard !booting else { return }
        booting = true
        showingError = false
        showSplash("INITIALISING CORE SYSTEMS")
        probe { alive in
            self.booting = false
            log(alive ? "boot: a core is already running, attaching" : "boot: starting core")
            if alive { self.web.load(coreRequest()) }  // already running (e.g. ./run.sh)
            else { self.startCore(); self.waitStarted = Date(); self.waitForCore() }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    func applicationWillTerminate(_ note: Notification) {
        log("quitting")
        quitting = true
        guard let p = core, p.isRunning else { return }
        p.terminate()  // SIGTERM: the core shuts down its listener and any Claude Code request
        let deadline = Date().addingTimeInterval(3)
        while p.isRunning && Date() < deadline { usleep(50_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }

    // MARK: core process
    func startCore() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = [projectDir + "/run.sh"]
        p.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        var env = ProcessInfo.processInfo.environment
        let home = NSHomeDirectory()
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        env["JARVIS_OPEN_BROWSER"] = "0"
        env["PYTHONUNBUFFERED"] = "1"
        env["JARVIS_PARENT_PID"] = String(getpid())  // the core exits if the app ever disappears
        p.environment = env
        let out = FileHandle(fileDescriptor: openLogFile(), closeOnDealloc: true)
        p.standardOutput = out
        p.standardError = out
        p.terminationHandler = { proc in
            DispatchQueue.main.async {
                let up = Int(Date().timeIntervalSince(self.coreStarted))
                log("core exited (status \(proc.terminationStatus), reason \(proc.terminationReason.rawValue), up \(up)s)")
                guard !self.quitting, self.core === proc else { return }
                self.core = nil
                // Crashed after running a while: restart it once (at most once a minute).
                if Date().timeIntervalSince(self.coreStarted) > 15 && Date().timeIntervalSince(self.lastAutoRestart) > 60 {
                    self.lastAutoRestart = Date()
                    self.boot()
                    return
                }
                self.showingError = true
                self.showSplash("CORE OFFLINE (exit \(proc.terminationStatus))", detail: "See ~/Library/Logs/JARVIS.log", restart: true)
            }
        }
        do { try p.run(); core = p; coreStarted = Date(); log("core started (pid \(p.processIdentifier))") }
        catch { log("core failed to start: \(error)"); showingError = true; showSplash("COULD NOT START CORE", detail: error.localizedDescription, restart: true) }
    }

    func probe(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: healthURL, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 1)
        req.httpMethod = "GET"
        probeSession.dataTask(with: req) { _, resp, _ in
            let ok = (resp as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    func waitForCore() {
        probe { alive in
            if alive { log("core ready after \(Int(Date().timeIntervalSince(self.waitStarted)))s"); self.web.load(coreRequest()); return }
            guard self.core != nil else { return }  // it exited; the splash already says so
            if Date().timeIntervalSince(self.waitStarted) > 240 {
                self.showingError = true
                self.showSplash("CORE NOT RESPONDING", detail: "See ~/Library/Logs/JARVIS.log", restart: true)
                return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { self.waitForCore() }
        }
    }

    @objc func restartCore() {
        if let p = core {
            core = nil  // its termination handler then ignores this exit
            if p.isRunning { p.terminate(); p.waitUntilExit() }
        }
        boot()
    }

    // MARK: window
    func buildWindow() {
        let cfg = WKWebViewConfiguration()
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.applicationNameForUserAgent = "JARVISApp/2.0"
        web = WKWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = self
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        web.underPageBackgroundColor = .black
        if #available(macOS 13.3, *) { web.isInspectable = true }

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "J.A.R.V.I.S."
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = .black
        window.appearance = NSAppearance(named: .darkAqua)
        window.minSize = NSSize(width: 900, height: 600)
        window.collectionBehavior = [.fullScreenPrimary]
        window.contentView = web
        window.isReleasedWhenClosed = false
        if !window.setFrameUsingName("JARVISMain") { window.center() }
        window.setFrameAutosaveName("JARVISMain")
        window.makeKeyAndOrderFront(nil)
    }

    func showSplash(_ title: String, detail: String = "", restart: Bool = false) {
        let button = restart ? "<a href=\"jarvis://restart\">RESTART CORE</a>" : ""
        let html = """
        <!doctype html><meta charset="utf-8"><style>
        html,body{margin:0;height:100%;background:#020608;color:#7fe9ff;font:600 13px/1.6 -apple-system,Helvetica,sans-serif;
          letter-spacing:.32em;display:flex;align-items:center;justify-content:center;flex-direction:column;-webkit-user-select:none;cursor:default}
        .r{width:150px;height:150px;border-radius:50%;border:2px solid #29d3ff;box-shadow:0 0 40px #29d3ff88,inset 0 0 40px #29d3ff55;
          position:relative;margin-bottom:42px;animation:p 2.4s ease-in-out infinite}
        .r:before{content:"";position:absolute;inset:22px;border-radius:50%;border:6px dashed #29d3ffaa;animation:s 7s linear infinite}
        .r:after{content:"";position:absolute;inset:58px;border-radius:50%;background:#bff6ff;box-shadow:0 0 34px 10px #29d3ff}
        @keyframes s{to{transform:rotate(360deg)}} @keyframes p{50%{box-shadow:0 0 70px #29d3ffbb,inset 0 0 50px #29d3ff77}}
        small{color:#4b8ea0;letter-spacing:.12em;margin-top:8px} a{margin-top:26px;color:#020608;background:#29d3ff;padding:9px 20px;
          border-radius:3px;text-decoration:none;letter-spacing:.24em}
        </style><div class="r"></div><div>\(title)</div><small>\(detail)</small>\(button)
        """
        web.loadHTMLString(html, baseURL: nil)
    }

    // MARK: web delegates
    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        // Only the local JARVIS interface may use the camera.
        decisionHandler(origin.host == "127.0.0.1" || origin.host == "localhost" ? .grant : .deny)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.allow) }
        if url.isFileURL { return decisionHandler(.cancel) }  // a file dropped outside the drop zone must not replace the HUD
        if url.scheme == "jarvis" {
            decisionHandler(.cancel)
            if url.host == "restart" { restartCore() }
            return
        }
        let local = url.host == "127.0.0.1" || url.host == "localhost"
        if (url.scheme == "http" || url.scheme == "https") && !local && action.targetFrame?.isMainFrame != false {
            NSWorkspace.shared.open(url)  // links go to the default browser
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    // The 📎 button: WebKit shows no file picker unless the app provides one.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowedContentTypes = [.image]
        panel.message = "Choose images for JARVIS"
        panel.prompt = "Attach"
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { retryLoad() }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { retryLoad() }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { web.load(coreRequest()) }

    func retryLoad() {
        guard !quitting, !showingError else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            self.probe { alive in
                if alive { self.web.load(coreRequest()) }
                else if self.core == nil { self.boot() }
            }
        }
    }

    // MARK: menu
    @objc func listen() { web.evaluateJavaScript("window.jarvis && window.jarvis.listen()") }
    @objc func stopListening() { web.evaluateJavaScript("window.jarvis && window.jarvis.stopListening()") }
    @objc func reload() { web.load(coreRequest()) }
    @objc func openLog() { NSWorkspace.shared.open(URL(fileURLWithPath: logPath)) }

    func buildMenu() {
        let main = NSMenu()
        func sub(_ title: String, _ items: [NSMenuItem]) {
            let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let m = NSMenu(title: title)
            items.forEach { m.addItem($0) }
            holder.submenu = m
            main.addItem(holder)
        }
        func item(_ t: String, _ a: Selector?, _ k: String = "", _ mods: NSEvent.ModifierFlags = .command, target: AnyObject? = nil) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k)
            i.keyEquivalentModifierMask = mods
            i.target = target
            return i
        }
        sub("JARVIS", [
            item("About JARVIS", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            item("Listen", #selector(listen), "h", target: self),  // the global ⌘H tap usually answers first
            item("Send (Stop Recording)", #selector(stopListening), "s", target: self),
            item("Restart Core", #selector(restartCore), target: self),
            item("Show Log", #selector(openLog), target: self),
            .separator(),
            item("Quit JARVIS", #selector(NSApplication.terminate(_:)), "q"),
        ])
        sub("Edit", [
            item("Undo", Selector(("undo:")), "z"), item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"), item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"), item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        sub("View", [
            item("Reload Interface", #selector(reload), "r", target: self),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        sub("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
// `kill` / logout: quit normally so the core is shut down too
signal(SIGTERM, SIG_IGN)
let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
termSource.setEventHandler { NSApp.terminate(nil) }
termSource.resume()
app.setActivationPolicy(.regular)
app.run()
