var base = '';
var token = '';

function setCreds(nextBase, nextToken) {
  base = String(nextBase || '');
  token = String(nextToken || '');
}

function xhr(method, path, jsonBody, cb) {
  var x = new XMLHttpRequest();
  var url = base.replace(/\/$/, '') + path;
  var t;
  var done = false;

  function finish(err, data) {
    if (done) return;
    done = true;
    if (t) clearTimeout(t);
    cb(err, data);
  }

  x.open(method, url, true);
  x.setRequestHeader('Accept', 'application/json');
  x.setRequestHeader('Content-Type', 'application/json');
  if (token) {
    x.setRequestHeader('X-Auth-Token', token);
    x.setRequestHeader('Authorization', 'Bearer ' + token);
  }
  x.onreadystatechange = function () {
    if (x.readyState !== 4) return;
    console.log('[http] ' + method + ' ' + path + ' ' + x.status);
    if (x.status === 401) return finish({status: 401, error: 'token'});
    if (x.status < 200 || x.status >= 300) {
      return finish({status: x.status, error: x.responseText});
    }
    try { finish(null, JSON.parse(x.responseText || '{}')); }
    catch (e) { finish({status: x.status, error: 'bad json'}); }
  };
  x.onerror = function () { finish({status: 0, error: 'daemon'}); };
  t = setTimeout(function () {
    try { x.abort(); } catch (e) {}
    finish({status: 0, error: 'daemon'});
  }, path.indexOf('/ping') >= 0 ? 8000 : 15000);
  x.send(jsonBody ? JSON.stringify(jsonBody) : null);
  return x;
}

function abortXhr(x) {
  if (!x) return;
  try { x.abort(); } catch (e) {}
}

/* One in-flight send (continue / new). Replacing aborts the previous XHR
 * without delivering its callback. */
function makeSlot() {
  var gen = 0;
  var x = null;
  return {
    busy: function () { return !!x; },
    abort: function () {
      gen++;
      abortXhr(x);
      x = null;
    },
    xhr: function (method, path, jsonBody, cb) {
      var my = ++gen;
      abortXhr(x);
      x = null;
      x = xhr(method, path, jsonBody, function (err, data) {
        if (my !== gen) return;
        x = null;
        cb(err, data);
      });
      return x;
    }
  };
}

module.exports = {
  setCreds: setCreds,
  xhr: xhr,
  abortXhr: abortXhr,
  makeSlot: makeSlot
};
