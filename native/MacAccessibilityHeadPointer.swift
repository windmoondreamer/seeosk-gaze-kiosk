import AppKit

/// Optional macOS-only handoff to the operating system's built-in head pointer.
enum MacAccessibilityHeadPointer {
    static func openSystemSettings() -> Bool {
        let settings = URL(fileURLWithPath: "/System/Applications/System Settings.app")
        return NSWorkspace.shared.open(settings)
    }
}
