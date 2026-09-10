var Clay = require('@rebble/clay');
var config = require('./config');

function customFn(minified) {
  var clayConfig = this;
  var $ = minified && minified.$;
  clayConfig.on(clayConfig.EVENTS.AFTER_BUILD, function () {
    var u = clayConfig.meta.userData || {};
    var form;
    var formEl;
    function set(id, v) {
      var item = clayConfig.getItemById(id);
      if (!item || v === undefined || v === null) return;
      item.set(v);
    }
    set('daemon_url', u.base || '');
    set('token', u.token || '');
    if (u.pollWork) set('poll_work', u.pollWork);
    if (u.pollIdle) set('poll_idle', u.pollIdle);
    if (u.font === 0 || u.font === 1 || u.font === 2) set('font_size', String(u.font));
    // serialize() drops fields with no messageKey; return values keyed by id.
    // Capture+stopImmediatePropagation so Clay's empty serialize() close does not win.
    form = $ ? $('#main-form') : clayConfig.$rootContainer;
    formEl = form && form[0];
    if (!formEl || !formEl.addEventListener) return;
    formEl.addEventListener('submit', function (e) {
      var out = {};
      var items = clayConfig.getAllItems();
      var i, item;
      var returnTo;
      if (e.preventDefault) e.preventDefault();
      if (e.stopImmediatePropagation) e.stopImmediatePropagation();
      if (e.stopPropagation) e.stopPropagation();
      for (i = 0; i < items.length; i++) {
        item = items[i];
        if (!item.id) continue;
        out[item.id] = { value: item.get() };
      }
      returnTo = (typeof window !== 'undefined' && window.returnTo)
        ? window.returnTo
        : 'pebblejs://close#';
      location.href = returnTo + encodeURIComponent(JSON.stringify(out));
    }, true);
  });
}

var clay = new Clay(config, customFn, {
  autoAppMessage: false,
  autoHandleEvents: false
});

module.exports = clay;
