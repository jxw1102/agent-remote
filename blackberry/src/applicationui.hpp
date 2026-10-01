#ifndef APPLICATIONUI_HPP
#define APPLICATIONUI_HPP

#include <QObject>

class ApiClient;

class ApplicationUI : public QObject
{
    Q_OBJECT
public:
    ApplicationUI();
    virtual ~ApplicationUI() {}

private Q_SLOTS:
    // Close must END the process. See applicationui.cpp.
    void armQuitWatchdog();
    // Diagnostic trace (trace.hpp): lifecycle + main-thread heartbeat.
    void heartbeat();
    void onFullscreen();
    void onThumbnail();
    void onInvisible();

private:
    ApiClient *m_api;
};

#endif // APPLICATIONUI_HPP
