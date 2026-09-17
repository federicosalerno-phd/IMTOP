/* ============================================================================
   bridge.js: the only file that talks to Qt.

   The Python side registers one object ("backend", see imtop/bridge.py) on a
   QWebChannel. Everything the UI asks of Python goes through be(); everything
   Python pushes back is a signal a module subscribed to with Bridge.on().

   Calls made before the channel is up are queued, so no module has to care
   about start-up order.
   ========================================================================== */

const Bridge = {
  obj: null,        // the remote "backend" object
  ready: false,
  _queue: [],       // calls made before the channel came up
  _subs: {},        // signal name -> [handler]

  /* Subscribe to a backend signal. Safe to call before the channel exists. */
  on: function (signal, fn) {
    if (!this._subs[signal]) this._subs[signal] = [];
    this._subs[signal].push(fn);
  },

  /* Connect the transport, then flush whatever queued up meanwhile. */
  init: function () {
    if (typeof qt === 'undefined' || !qt.webChannelTransport) {
      setTimeout(function () { Bridge.init(); }, 80);
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      Bridge.obj = channel.objects.backend;
      Bridge.ready = true;
      Bridge._connect();
      Bridge._flush();
    });
  },

  _connect: function () {
    for (const signal in this._subs) {
      const emitter = this.obj[signal];
      if (!emitter || !emitter.connect) { console.warn('bridge: no signal', signal); continue; }
      this._subs[signal].forEach(function (fn) { emitter.connect(fn); });
    }
  },

  _flush: function () {
    while (this._queue.length) {
      const c = this._queue.shift();
      this._invoke(c.m, c.a, c.cb);
    }
  },

  _invoke: function (m, a, cb) {
    const fn = this.obj[m];
    if (!fn) { console.error('bridge: no slot', m); return; }
    const done = cb || function () {};
    if (a === undefined || a === null) fn(done);
    else fn(a, done);
  },
};

/* Call a backend slot: be('setScale', 50.0, r => …).
   `arg` is undefined for no-argument slots; `cb` receives the slot's return
   value (already decoded by QWebChannel, JSON strings still need parsing). */
function be(method, arg, cb) {
  if (Bridge.ready) Bridge._invoke(method, arg, cb);
  else Bridge._queue.push({ m: method, a: arg, cb: cb });
}

/* Same, for slots that answer with a JSON string. */
function beJson(method, arg, cb) {
  be(method, arg, function (raw) {
    let parsed = null;
    try { parsed = JSON.parse(raw); } catch (e) { console.error('bridge: bad JSON from', method, raw); }
    cb(parsed);
  });
}
