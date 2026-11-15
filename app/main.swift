// Native shell for the dashboard: starts server.py, shows it in a window,
// and stops the server again when the app quits.
import Cocoa
import WebKit

let port = 8787
let repo = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : FileManager.default.currentDirectoryPath
let url = URL(string: "http://127.0.0.1:\(port)/")!

func portOpen() -> Bool {
    let s = socket(AF_INET, SOCK_STREAM, 0)
    defer { close(s) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = in_port_t(port).bigEndian
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    return withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(s, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var server: Process?
    var window: NSWindow!
    var web: WKWebView!

    func startServer() {
        guard !portOpen() else { return }  // someone else already serves it; leave it alone
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3", "server.py", "--port", "\(port)"]
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        let log = FileHandle(forWritingAtPath: "/tmp/cc-insights.log") ?? {
            FileManager.default.createFile(atPath: "/tmp/cc-insights.log", contents: nil)
            return FileHandle(forWritingAtPath: "/tmp/cc-insights.log")!
        }()
        p.standardOutput = log
        p.standardError = log
        try? p.run()
        server = p
        for _ in 0..<100 where !portOpen() { usleep(100_000) }
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        startServer()
        buildMenu()

        // The page reports its background colour so the title bar can blend with it.
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(self, name: "bg")
        cfg.userContentController.addUserScript(WKUserScript(source: """
            (() => {
              const send = () => window.webkit.messageHandlers.bg.postMessage(getComputedStyle(document.body).backgroundColor);
              send();
              matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => setTimeout(send, 0));
              new MutationObserver(() => setTimeout(send, 0)).observe(document.documentElement, { attributes: true });
            })();
            """, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = self
        web.allowsBackForwardNavigationGestures = true
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "CC Insights"
        window.titlebarAppearsTransparent = true
        window.contentView = web
        window.delegate = self
        window.setFrameAutosaveName("main")
        if !window.setFrameUsingName("main") { window.center() }
        window.makeKeyAndOrderFront(nil)
        web.load(URLRequest(url: url))
        NSApp.activate(ignoringOtherApps: true)
    }

    // Keep external links out of the app window.
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = a.request.url, u.host != "127.0.0.1", u.scheme?.hasPrefix("http") == true {
            NSWorkspace.shared.open(u)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let css = m.body as? String else { return }
        let n = css.components(separatedBy: CharacterSet(charactersIn: "0123456789.").inverted)
            .compactMap { Double($0) }
        guard n.count >= 3 else { return }
        window.backgroundColor = NSColor(srgbRed: n[0] / 255, green: n[1] / 255, blue: n[2] / 255, alpha: 1)
    }

    @objc func reload() { web.reload() }
    func windowWillClose(_ n: Notification) { NSApp.terminate(nil) }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ n: Notification) { server?.terminate() }

    func buildMenu() {
        let main = NSMenu()
        func item(_ title: String, _ menu: NSMenu) -> NSMenu {
            let i = NSMenuItem(); main.addItem(i); i.submenu = menu; menu.title = title; return menu
        }
        let app = item("CC Insights", NSMenu())
        app.addItem(withTitle: "Quit CC Insights", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let edit = item("Edit", NSMenu(title: "Edit"))
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let view = item("View", NSMenu(title: "View"))
        let r = view.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r"); r.target = self
        let win = item("Window", NSMenu(title: "Window"))
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        NSApp.mainMenu = main
        NSApp.windowsMenu = win
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
signal(SIGTERM) { _ in DispatchQueue.main.async { NSApp.terminate(nil) } }
app.run()
