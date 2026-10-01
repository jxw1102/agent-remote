#ifndef QMLRES_HPP
#define QMLRES_HPP

#include <bb/cascades/QmlDocument>

#include <QDebug>
#include <QString>

// Canonical copy of the loader each Cascades app compiles in.
// Apps keep a local copy next to their sources: the NDK build copies do not
// share one include path. Do not edit a local copy; change this file and
// re-copy it.
//
// Qt 4.8 has no bytecode form of QML. rcc embeds the document text and
// QDeclarative still parses it. assets/ stays on disk so a resource that
// will not build a scene can fall back to the path that already worked.

namespace qmlres {

inline QString qrcUrlFor(const QString &url)
{
    if (url.startsWith(QLatin1String("qrc:///")))
        return url;
    if (url.startsWith(QLatin1String("asset:///")))
        return QLatin1String("qrc:///") + url.mid(9);
    return QLatin1String("qrc:///") + url;
}

inline QString assetUrlFor(const QString &url)
{
    if (url.startsWith(QLatin1String("asset:///")))
        return url;
    if (url.startsWith(QLatin1String("qrc:///")))
        return QLatin1String("asset:///") + url.mid(7);
    return QLatin1String("asset:///") + url;
}

// pass 0 is the compiled-in document, pass 1 is assets/ on disk.
inline QString urlForPass(const QString &url, int pass)
{
    return pass == 0 ? qrcUrlFor(url) : assetUrlFor(url);
}

inline void noteScene(int pass)
{
    if (pass == 0)
        qDebug("qml: compiled-in (qrc)");
    else if (pass == 1)
        qDebug("qml: assets/ on disk");
}

} // namespace qmlres

#endif
