import AppKit
import WebKit
import AVFoundation
import CoreGraphics

final class SeeOSKDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var worker: Process?
    var input: FileHandle?
    var output: Pipe?
    var errorLog: FileHandle?
    var buffer = Data()
    let readQueue = DispatchQueue(label: "org.seeosk.worker-output")
    let resources = Bundle.main.resourceURL!
    let support = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/SeeOSK")
    var uiReady = false
    var quitting = false
    var smokeStarted = false
    let smoke = CommandLine.arguments.contains("--smoke-test")
    let cameraCheck = CommandLine.arguments.contains("--camera-check")
    var smokeFolder: URL {
        if let i = CommandLine.arguments.firstIndex(of: "--output"), CommandLine.arguments.count > i+1 {
            return URL(fileURLWithPath: CommandLine.arguments[i+1], isDirectory: true)
        }
        return support.appendingPathComponent("verification")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        for directory in ["matplotlib"] {
            try? FileManager.default.createDirectory(at:support.appendingPathComponent(directory),withIntermediateDirectories:true)
        }
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "SeeOSK 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu; menu.addItem(appItem)
        NSApp.mainMenu = menu
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "seeosk")
        config.userContentController.addUserScript(WKUserScript(source: "window.addEventListener('error', e => window.webkit.messageHandlers.seeosk.postMessage({cmd:'js_error',message:e.message,source:e.filename,line:e.lineno}));", injectionTime: .atDocumentStart, forMainFrameOnly: false))
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self; web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        web.isInspectable = true
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x:0,y:0,width:1400,height:900)
        let size = NSSize(width:min(1320,screen.width-32),height:min(900,screen.height-32))
        window = NSWindow(contentRect:NSRect(origin:.zero,size:size),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title = "SeeOSK · 고개로 주문"
        window.minSize = NSSize(width:980,height:700)
        window.contentView = web; window.delegate = self
        window.center(); window.makeKeyAndOrderFront(nil)
        window.collectionBehavior = [.fullScreenPrimary]
        NSApp.activate(ignoringOtherApps: true)
        let page = resources.appendingPathComponent("web/index.html")
        web.loadFileURL(page, allowingReadAccessTo: resources.appendingPathComponent("web"))
        if smoke {
            DispatchQueue.main.asyncAfter(deadline:.now()+90) { [weak self] in
                guard let self=self, !self.quitting else {return}
                self.finishSmoke(["passed":false,"error":"Native smoke test timed out"])
            }
        }
        if cameraCheck {
            let status=AVCaptureDevice.authorizationStatus(for:.video)
            log("camera-check: authorizationStatus=\(status.rawValue) (3=authorized)")
            DispatchQueue.main.asyncAfter(deadline:.now()+3) { [weak self] in
                guard let self=self else {return}
                self.startWorker()
                DispatchQueue.main.asyncAfter(deadline:.now()+8) {
                    self.log("camera-check: sending start")
                    self.command(["cmd":"start","camera":-1])
                    DispatchQueue.main.asyncAfter(deadline:.now()+20) {
                        self.log("camera-check: done")
                        NSApp.terminate(nil)
                    }
                }
            }
        }
    }

    func event(_ object: [String:Any]) {
        if cameraCheck, let type=object["type"] as? String, type != "tracking" {
            log("camera-check event: \(object)")
        }
        guard uiReady, let data=try? JSONSerialization.data(withJSONObject:object),let text=String(data:data,encoding:.utf8) else{return}
        web.evaluateJavaScript("window.receive(\(text))") { _, error in
            if let error=error { self.log("UI event error: \(error)") }
        }
    }

    func log(_ message: String) {
        try? errorLog?.write(contentsOf:Data((message+"\n").utf8))
    }

    func startWorker() {
        guard worker == nil else{return}
        do {
            let process=Process()
            let bundled=resources.appendingPathComponent("runtime/SeeOSKEngine")
            if FileManager.default.isExecutableFile(atPath:bundled.path) {
                process.executableURL=bundled
                process.arguments=[]
            } else {
                let data=try Data(contentsOf:resources.appendingPathComponent("runtime.json"))
                let settings=try JSONSerialization.jsonObject(with:data) as! [String:String]
                guard let python=settings["python"],FileManager.default.isExecutableFile(atPath:python) else {
                    event(["type":"error","message":"앱의 실행 엔진이 누락되었습니다. 배포 파일을 다시 받아 주세요."]);return
                }
                process.executableURL=URL(fileURLWithPath:python)
                process.arguments=["-B","-u",resources.appendingPathComponent("engine/worker.py").path]
            }
            process.currentDirectoryURL=resources
            var env=ProcessInfo.processInfo.environment
            env["SEE_OSK_RESOURCES"]=resources.path
            env["PYTHONUNBUFFERED"]="1";env["PYTHONNOUSERSITE"]="1"
            env["PYTHONDONTWRITEBYTECODE"]="1"
            env["MPLCONFIGDIR"]=support.appendingPathComponent("matplotlib").path
            process.environment=env
            let incoming=Pipe(),outgoing=Pipe()
            input=incoming.fileHandleForWriting;output=outgoing
            process.standardInput=incoming;process.standardOutput=outgoing
            let logURL=support.appendingPathComponent("engine.log")
            FileManager.default.createFile(atPath:logURL.path,contents:nil)
            errorLog=try FileHandle(forWritingTo:logURL)
            process.standardError=errorLog
            outgoing.fileHandleForReading.readabilityHandler={ [weak self] handle in
                let data=handle.availableData
                guard !data.isEmpty else {handle.readabilityHandler=nil;return}
                self?.readQueue.async { [weak self] in self?.consume(data) }
            }
            process.terminationHandler={ [weak self] p in
                DispatchQueue.main.async {
                    guard let self=self,!self.quitting else{return}
                    self.event(["type":"engine_exit","status":p.terminationStatus])
                }
            }
            try process.run();worker=process;log("Worker started PID \(process.processIdentifier)")
        } catch {event(["type":"error","message":"엔진을 시작하지 못했습니다: \(error.localizedDescription)"])}
    }

    func consume(_ data:Data) {
        buffer.append(data)
        while let newline=buffer.firstIndex(of:10) {
            let line=buffer.prefix(upTo:newline)
            buffer.removeSubrange(...newline)
            if let value=try? JSONSerialization.jsonObject(with:line) as? [String:Any] {
                DispatchQueue.main.async {self.event(value)}
            }
        }
        if buffer.count>2_000_000 {buffer.removeAll()}
    }

    func command(_ data:[String:Any]) {
        guard let input=input,let json=try? JSONSerialization.data(withJSONObject:data) else{return}
        do {try input.write(contentsOf:json+Data([10]))}catch{event(["type":"error","message":"추적 엔진과 연결이 끊겼습니다."])}
    }

    func permissionStart(_ message:[String:Any]) {
        let status=AVCaptureDevice.authorizationStatus(for:.video)
        if status == .authorized {command(message)}
        else if status == .notDetermined {
            AVCaptureDevice.requestAccess(for:.video) { granted in
                DispatchQueue.main.async {if granted {self.command(message)}else{self.permissionDenied()}}
            }
        } else {permissionDenied()}
    }
    func permissionDenied(){event(["type":"permission_denied","message":"시스템 설정 → 개인정보 보호 및 보안 → 카메라에서 SeeOSK를 허용해 주세요."])}

    func userContentController(_ userContentController:WKUserContentController,didReceive message:WKScriptMessage) {
        guard message.frameInfo.isMainFrame,let m=message.body as? [String:Any],let cmd=m["cmd"] as? String else{return}
        switch cmd {
        case "ui_ready":uiReady=true;startWorker()
        case "js_error":log("JavaScript: \(m)")
        case "start":if !smoke {permissionStart(m)}
        case "configure":
            guard var geometry=m["geometry"] as? [String:Any] else{return}
            geometry["window"]=[Int(window.frame.origin.x.rounded()),Int(window.frame.origin.y.rounded()),Int(window.frame.width.rounded()),Int(window.frame.height.rounded())]
            geometry["scale"]=window.backingScaleFactor
            command(["cmd":"configure","geometry":geometry])
        case "fullscreen":window.toggleFullScreen(nil)
        case "permissions":NSWorkspace.shared.open(URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_Camera")!)
        case "move_cursor":
            if !moveCursor(m) && window.isKeyWindow {log("Cursor movement rejected or failed")}
        case "cursor_test":if smoke {
            NSApp.activate(ignoringOtherApps:true)
            window.makeKeyAndOrderFront(nil)
            DispatchQueue.main.asyncAfter(deadline:.now()+0.4) {
                let original=CGEvent(source:nil)?.location
                let moved=self.moveCursor(m)
                let local=NSPoint(x:m["x"] as? Double ?? 0,y:m["y"] as? Double ?? 0)
                let origin=self.window.convertPoint(toScreen:self.web.convert(NSPoint(x:0,y:self.web.isFlipped ? 0 : self.web.bounds.height),to:nil))
                let expected=CGPoint(x:origin.x+local.x,y:(NSScreen.screens.first?.frame.maxY ?? 0)-origin.y+local.y)
                DispatchQueue.main.asyncAfter(deadline:.now()+0.05) {
                    let actual=CGEvent(source:nil)?.location ?? .zero
                    let error=hypot(actual.x-expected.x,actual.y-expected.y)
                    if let original=original {CGWarpMouseCursorPosition(original)}
                    self.event(["type":"cursor_test_result","passed":moved && error<2,"error_px":error])
                }
            }
        }
        case "snapshot":snapshot(m)
        case "detect_sample":
            guard let name=m["filename"] as? String,!name.contains("/"),name.hasSuffix(".png") else{return}
            command(["cmd":"detect","path":resources.appendingPathComponent("web/samples").appendingPathComponent(name).path,"request":m["request"] ?? 0])
        case "smoke_result":if smoke {finishSmoke(m)}
        default:command(m)
        }
    }

    // Gaze coordinates are CSS points in the web content, not Retina pixels.
    func moveCursor(_ m:[String:Any]) -> Bool {
        guard window.isKeyWindow, NSApp.isActive,
              let x=m["x"] as? Double, let y=m["y"] as? Double,
              x.isFinite, y.isFinite, x>=0, y>=0,
              x<=web.bounds.width, y<=web.bounds.height else {return false}
        let local=NSPoint(x:min(x,web.bounds.width-1), y:web.isFlipped ? min(y,web.bounds.height-1) : web.bounds.height-min(y,web.bounds.height-1))
        let screen=window.convertPoint(toScreen:web.convert(local,to:nil))
        let point=CGPoint(x:screen.x,y:(NSScreen.screens.first?.frame.maxY ?? 0)-screen.y)
        return CGWarpMouseCursorPosition(point) == .success
    }

    func snapshot(_ message:[String:Any]) {
        guard let r=message["rect"] as? [String:Double],let x=r["x"],let y=r["y"],let w=r["w"],let h=r["h"],w>0,h>0 else{return}
        let config=WKSnapshotConfiguration()
        config.rect=CGRect(x:x,y:y,width:w,height:h).intersection(web.bounds)
        config.snapshotWidth=NSNumber(value:w)
        web.takeSnapshot(with:config) { image,error in
            self.event(["type":"capture_done","request":message["request"] ?? 0])
            guard let image=image,let tiff=image.tiffRepresentation,let bitmap=NSBitmapImageRep(data:tiff),let data=bitmap.representation(using:.png,properties:[:]) else {
                self.event(["type":"detector_error","request":message["request"] ?? 0,"message":"화면을 캡처하지 못했습니다."]);return
            }
            do {
                let folder=self.support.appendingPathComponent("snapshots")
                try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
                let url=folder.appendingPathComponent(UUID().uuidString+".png")
                try data.write(to:url)
                self.command(["cmd":"detect","path":url.path,"request":message["request"] ?? 0,"temporary":true])
            } catch {self.event(["type":"error","message":error.localizedDescription])}
        }
    }

    func webView(_ webView:WKWebView,didFinish navigation:WKNavigation!) {
        if smoke && !smokeStarted {
            smokeStarted=true
            DispatchQueue.main.asyncAfter(deadline:.now()+2) {
                if let script=try? String(contentsOf:self.resources.appendingPathComponent("smoke.js"),encoding:.utf8){self.web.evaluateJavaScript(script)}
            }
        }
    }
    func webView(_ webView:WKWebView,decidePolicyFor navigationAction:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void){
        guard let url=navigationAction.request.url else{decisionHandler(.cancel);return}
        let localFile = url.isFileURL && url.path.hasPrefix(resources.appendingPathComponent("web").path)
        let embedded = navigationAction.targetFrame?.isMainFrame == false && ["about:srcdoc","about:blank"].contains(url.absoluteString)
        decisionHandler(localFile || embedded ? .allow : .cancel)
    }
    func webView(_ webView:WKWebView,runJavaScriptAlertPanelWithMessage message:String,initiatedByFrame frame:WKFrameInfo,completionHandler:@escaping()->Void){
        let alert=NSAlert();alert.messageText="키오스크 연습";alert.informativeText=message;alert.addButton(withTitle:"확인")
        alert.beginSheetModal(for:window){_ in completionHandler()}
    }
    func windowDidMove(_ notification:Notification){if uiReady{event(["type":"window_changed"])}}
    func windowDidChangeBackingProperties(_ notification:Notification){if uiReady{event(["type":"window_changed"])}}
    func windowDidResignKey(_ notification:Notification){if uiReady{event(["type":"focus","active":false])}}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool{true}
    func applicationWillTerminate(_ notification:Notification){
        quitting=true;command(["cmd":"shutdown"])
        try? input?.close();output?.fileHandleForReading.readabilityHandler=nil
        if let worker=worker,worker.isRunning{worker.terminate()}
        try? errorLog?.close()
    }
    func finishSmoke(_ result:[String:Any]) {
        guard !quitting else{return};quitting=true
        try? FileManager.default.createDirectory(at:smokeFolder,withIntermediateDirectories:true)
        if let data=try? JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]){try? data.write(to:smokeFolder.appendingPathComponent("native-smoke.json"))}
        web.takeSnapshot(with:nil){ image,_ in
            if let tiff=image?.tiffRepresentation,let bitmap=NSBitmapImageRep(data:tiff),let png=bitmap.representation(using:.png,properties:[:]){try? png.write(to:self.smokeFolder.appendingPathComponent("app.png"))}
            NSApp.terminate(nil)
        }
    }
}

let app=NSApplication.shared
let delegate=SeeOSKDelegate()
app.delegate=delegate
app.run()
