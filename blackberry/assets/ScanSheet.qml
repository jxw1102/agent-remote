import bb.cascades 1.4
import bb.cascades.multimedia 1.0
import bb.multimedia 1.0

/**
 * Reads a daemon off the web client's screen.
 *
 * Agent Remote on the web draws agentremote://profile?url=…&token=…&name=…
 * as a QR code at the bottom of its daemon form. Pointing the camera at it
 * beats typing a 40-character token on a phone keyboard.
 *
 * Camera + BarcodeDetector is the recipe proven on-device in Authenticator;
 * the paste box below is the fallback for a phone with no camera permission
 * (and for a pairing link that arrived by message).
 */
Sheet {
    id: scanSheet

    // Pinned by SettingsSheet (api: settingsSheet.api) - no bare `_api` here.
    property variant api
    // { name, baseUrl, token } once a pairing code has been read.
    signal paired(variant data)

    peekEnabled: false

    // One decoded frame is enough; the detector keeps firing at ~10 Hz.
    property bool handled: false

    function show() {
        handled = false;
        pasteField.text = "";
        statusLabel.text = qsTr("Opening the camera...");
        open();
    }

    function consider(data) {
        if (handled || ! data || data.length === 0 || ! scanSheet.api)
            return;
        var parsed = scanSheet.api.parsePairingUri("" + data);
        if (! parsed || parsed.ok !== true) {
            statusLabel.text = qsTr("That code is not an Agent Remote daemon. Open the web client, edit the daemon, and point at the code under the token.");
            return;
        }
        handled = true;
        scanSheet.paired(parsed);
        scanSheet.close();
    }

    onOpened: {
        camera.open();
    }
    onClosed: {
        camera.close();
    }

    Page {
        titleBar: TitleBar {
            title: qsTr("Scan a daemon")
            dismissAction: ActionItem {
                title: qsTr("Cancel")
                onTriggered: scanSheet.close()
            }
        }

        Container {
            layout: DockLayout {}
            background: Color.Black
            horizontalAlignment: HorizontalAlignment.Fill
            verticalAlignment: VerticalAlignment.Fill

            Camera {
                id: camera
                horizontalAlignment: HorizontalAlignment.Fill
                verticalAlignment: VerticalAlignment.Fill

                onCameraOpened: {
                    camera.getSettings(cameraSettings);
                    cameraSettings.focusMode = CameraFocusMode.ContinuousAuto;
                    camera.applySettings(cameraSettings);
                    camera.startViewfinder();
                    statusLabel.text = qsTr("Point at the pairing code on the Agent Remote web page.");
                }

                onCameraOpenFailed: {
                    statusLabel.text = qsTr("The camera would not open. Paste the pairing link below instead.");
                }

                attachedObjects: [
                    BarcodeDetector {
                        id: barcodeDetector
                        camera: camera
                        formats: BarcodeFormat.QrCode
                        onDetected: {
                            scanSheet.consider(data);
                        }
                    },
                    CameraSettings {
                        id: cameraSettings
                    }
                ]
            }

            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                verticalAlignment: VerticalAlignment.Bottom
                background: Color.create("#CC101014")
                leftPadding: 16
                rightPadding: 16
                topPadding: 12
                bottomPadding: 16

                Label {
                    id: statusLabel
                    text: qsTr("Opening the camera...")
                    multiline: true
                    textStyle.color: Color.White
                    textStyle.fontSize: FontSize.Small
                }

                TextField {
                    id: pasteField
                    topMargin: 10
                    hintText: qsTr("Or paste agentremote://profile?... here")
                    inputMode: TextFieldInputMode.Url
                }

                Button {
                    text: qsTr("Use pasted link")
                    topMargin: 10
                    horizontalAlignment: HorizontalAlignment.Fill
                    onClicked: {
                        scanSheet.consider(pasteField.text);
                    }
                }
            }
        }
    }
}
