#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform notification support for RepoMap.

Supports:
- Windows: Toast notifications via PowerShell
- macOS: osascript notifications
- Linux: notify-send (if available)
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional


def send_notification(
    title: str,
    message: str,
    app_name: str = "RepoMap",
    sound: bool = True
) -> bool:
    """
    Send a system notification.

    Args:
        title: Notification title
        message: Notification message
        app_name: Application name (used on some platforms)
        sound: Whether to play notification sound

    Returns:
        True if notification was sent successfully
    """
    if sys.platform == 'win32':
        return _send_windows_notification(title, message, app_name, sound)
    elif sys.platform == 'darwin':
        return _send_macos_notification(title, message, app_name, sound)
    else:
        return _send_linux_notification(title, message, app_name)


def _send_windows_notification(
    title: str,
    message: str,
    app_name: str,
    sound: bool
) -> bool:
    """Send Windows Toast notification via PowerShell"""
    # Escape special characters for PowerShell
    title = title.replace("'", "''").replace('"', '`"')
    message = message.replace("'", "''").replace('"', '`"')
    app_name = app_name.replace("'", "''").replace('"', '`"')

    # Build Toast XML
    audio_element = '<audio src="ms-winsoundevent:Notification.Default"/>' if sound else ''

    ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title}</text>
            <text id="2">{message}</text>
        </binding>
    </visual>
    {audio_element}
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_name}")
$notifier.Show($toast)
'''

    try:
        # Use CREATE_NO_WINDOW to hide PowerShell window
        creation_flags = 0
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creation_flags = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-Command', ps_script],
            capture_output=True,
            text=True,
            creationflags=creation_flags
        )
        return result.returncode == 0
    except Exception:
        return False


def _send_macos_notification(
    title: str,
    message: str,
    app_name: str,
    sound: bool
) -> bool:
    """Send macOS notification via osascript"""
    # Escape for AppleScript
    title = title.replace('"', '\\"')
    message = message.replace('"', '\\"')

    sound_part = 'sound name "default"' if sound else ''

    script = f'display notification "{message}" with title "{title}" {sound_part}'

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def _send_linux_notification(
    title: str,
    message: str,
    app_name: str
) -> bool:
    """Send Linux notification via notify-send"""
    try:
        result = subprocess.run(
            ['notify-send', '-a', app_name, title, message],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        # notify-send not available
        return False
    except Exception:
        return False


def create_windows_notify_script(output_path: Path) -> Path:
    """
    Create a standalone PowerShell notification script.

    This can be used from Git hooks or other external scripts.

    Args:
        output_path: Directory to create the script in

    Returns:
        Path to the created script
    """
    script_content = '''# PowerShell Toast Notification Script
# Usage: powershell -ExecutionPolicy Bypass -File notify.ps1 -Title "Title" -Message "Message"

param(
    [Parameter(Mandatory=$true)]
    [string]$Title,

    [Parameter(Mandatory=$true)]
    [string]$Message,

    [string]$AppName = "RepoMap"
)

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">$Title</text>
            <text id="2">$Message</text>
        </binding>
    </visual>
    <audio src="ms-winsoundevent:Notification.Default"/>
</toast>
"@

try {
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml($template)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppName)
    $notifier.Show($toast)
} catch {
    Write-Host "Notification failed: $_" -ForegroundColor Yellow
}
'''

    script_path = output_path / 'notify.ps1'
    script_path.write_text(script_content, encoding='utf-8')
    return script_path


if __name__ == "__main__":
    # Test notification
    import argparse

    parser = argparse.ArgumentParser(description='Send a system notification')
    parser.add_argument('--title', '-t', default='RepoMap', help='Notification title')
    parser.add_argument('--message', '-m', default='Test notification', help='Notification message')
    parser.add_argument('--no-sound', action='store_true', help='Disable sound')

    args = parser.parse_args()

    success = send_notification(
        title=args.title,
        message=args.message,
        sound=not args.no_sound
    )

    print(f"Notification sent: {success}")
    sys.exit(0 if success else 1)
