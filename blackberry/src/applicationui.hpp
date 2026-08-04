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

private:
    ApiClient *m_api;
};

#endif // APPLICATIONUI_HPP
