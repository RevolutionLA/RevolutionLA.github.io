/* ============================================================
   RevolutionLA · SIGMA INSTRUMENT
   仪器的前面板：开机校准 → 解码 → 实时读数 → 滚动编排
   ------------------------------------------------------------
   三条硬规矩
     1. 任何动画都要给 prefers-reduced-motion 一条静态出路（不是「删掉」，
        而是「渲染成最终态」）。
     2. 所有绘制走一个 rAF 循环；页面不可见、或画布不在视口内时停表。
     3. JS 缺席时页面必须完整可读：装饰件（光标/示波器/开机）自行隐身。
   ============================================================ */

(function () {
  'use strict';

  var doc = document;
  var root = doc.documentElement;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  /* ==========================================================
     0 · 单一 rAF 调度器
     ========================================================== */
  var ticker = (function () {
    var jobs = [];
    var raf = null;
    var last = 0;
    function loop(ts) {
      var dt = Math.min(48, ts - last || 16);
      last = ts;
      for (var i = jobs.length - 1; i >= 0; i--) {
        if (jobs[i](ts, dt) === false) jobs.splice(i, 1);
      }
      raf = jobs.length ? requestAnimationFrame(loop) : null;
    }
    return {
      add: function (fn) {
        if (jobs.indexOf(fn) < 0) jobs.push(fn);
        if (raf === null) { last = 0; raf = requestAnimationFrame(loop); }
      },
      remove: function (fn) {
        var i = jobs.indexOf(fn);
        if (i >= 0) jobs.splice(i, 1);
      },
      /* 页面切到后台时停表，回来再续：省电，也不会让 t 累加出跳变 */
      stop: function () {
        if (raf !== null) { cancelAnimationFrame(raf); raf = null; }
      },
      start: function () {
        if (raf === null && jobs.length) { last = 0; raf = requestAnimationFrame(loop); }
      }
    };
  })();

  doc.addEventListener('visibilitychange', function () {
    if (doc.hidden) ticker.stop(); else ticker.start();
  });

  /* ==========================================================
     1 · 时钟（成都 = UTC+8，不依赖访客本机时区）
     ========================================================== */
  var clocks = [].slice.call(doc.querySelectorAll('[data-clock]'));
  function tickClock() {
    if (!clocks.length) return;
    var now = new Date();
    var bj = new Date(now.getTime() + now.getTimezoneOffset() * 60000 + 8 * 3600000);
    var t = [bj.getHours(), bj.getMinutes(), bj.getSeconds()]
      .map(function (n) { return (n < 10 ? '0' : '') + n; }).join(':');
    for (var i = 0; i < clocks.length; i++) clocks[i].textContent = t;
  }
  tickClock();
  setInterval(tickClock, 1000);

  /* ==========================================================
     2 · 开机校准序列
     ========================================================== */
  var boot = doc.getElementById('boot');
  var booted = false;
  try { booted = sessionStorage.getItem('sigma.booted') === '1'; } catch (e) {}
  var willBoot = !!boot && !booted && !reduced;

  /* 只有真的要放开机动画时才锁滚动、露出遮罩；否则它必须完全不存在，
     免得 JS 报错或用户禁用 JS 时，页面被一层黑幕盖住。 */
  if (boot) {
    if (willBoot) {
      root.classList.add('is-booting');
      boot.classList.add('is-live');
    } else {
      boot.parentNode.removeChild(boot);
    }
  }

  function startBoot(done) {
    if (!willBoot) { done(); return; }
    boot.classList.add('go');
    var pct = boot.querySelector('.boot-pct');
    var t0 = null;
    var DUR = 1150;
    function step(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / DUR);
      if (pct) pct.firstChild.nodeValue = String(Math.round(p * 100)).padStart(3, '0');
      if (p < 1) { requestAnimationFrame(step); return; }
      try { sessionStorage.setItem('sigma.booted', '1'); } catch (e) {}
      boot.classList.add('is-done');
      root.classList.remove('is-booting');
      setTimeout(done, 260);
    }
    requestAnimationFrame(step);
  }

  /* ==========================================================
     3 · 文字解码（Latin 用符号池，CJK 用汉字池）
     ========================================================== */
  var POOL_EN = 'ABCDEFGHKLMNPRSTUVXZ#$%&*+=<>/\\|0123456789';
  var POOL_CN = '昂技术信号可能硬件软件云端边缘体系结构探索极';
  function decode(el, delay) {
    var target = el.getAttribute('data-decode') || el.textContent;
    if (reduced) { el.textContent = target; return; }
    var isCN = /[一-鿿]/.test(target);
    var pool = isCN ? POOL_CN : POOL_EN;
    var chars = target.split('');
    var frame = 0;
    var settle = chars.map(function (_, i) { return 4 + i * 1.6; });
    el.textContent = target;
    el.style.visibility = 'hidden';
    setTimeout(function () {
      el.style.visibility = 'visible';
      function run() {
        var out = '';
        var done = true;
        for (var i = 0; i < chars.length; i++) {
          if (chars[i] === ' ') { out += ' '; continue; }
          if (frame >= settle[i]) { out += chars[i]; continue; }
          out += pool[Math.floor(Math.random() * pool.length)];
          done = false;
        }
        el.textContent = out;
        frame++;
        if (!done) requestAnimationFrame(run);
        else el.textContent = target;
      }
      run();
    }, delay || 0);
  }

  /* ==========================================================
     4 · 星野（三层视差 + 呼吸）
     ========================================================== */
  (function starfield() {
    var cv = doc.getElementById('starfield');
    var ctx = cv && cv.getContext && cv.getContext('2d');
    if (!ctx) return;
    var w = 0, h = 0, dpr = 1, stars = [], scrollY = 0;

    function build() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = doc.documentElement.clientWidth || window.innerWidth;
      h = doc.documentElement.clientHeight || window.innerHeight;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var n = Math.min(Math.round(Math.min(w, 1600) * 0.055), 130);
      stars = [];
      for (var i = 0; i < n; i++) {
        var layer = i % 3;
        stars.push({
          x: Math.random() * w,
          y: Math.random() * h,
          r: [0.5, 0.9, 1.5][layer] * (0.6 + Math.random() * 0.8),
          a: [0.16, 0.3, 0.5][layer] * (0.5 + Math.random() * 0.7),
          par: [0.02, 0.05, 0.1][layer],
          tw: Math.random() * 6.28,
          sp: 0.0008 + Math.random() * 0.0026,
          green: Math.random() < 0.07
        });
      }
    }

    function paint(ts) {
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i];
        var a = reduced ? s.a : s.a * (0.62 + 0.38 * Math.sin(s.tw));
        var y = s.y - (scrollY * s.par) % h;
        if (y < -4) y += h;
        ctx.beginPath();
        ctx.arc(s.x, y, s.r, 0, 6.2832);
        ctx.fillStyle = s.green
          ? 'rgba(52,240,160,' + (a * 0.9).toFixed(3) + ')'
          : 'rgba(226,236,246,' + a.toFixed(3) + ')';
        ctx.fill();
        if (!reduced) s.tw += s.sp * 16;
      }
    }

    build();
    window.addEventListener('resize', function () { build(); if (reduced) paint(); }, { passive: true });
    window.addEventListener('scroll', function () { scrollY = window.scrollY || 0; }, { passive: true });

    if (reduced) { paint(); }
    else {
      ticker.add(function (ts) { paint(ts); });
    }
  })();

  /* ==========================================================
     5 · 示波器（指针调制 + 余辉）
     ========================================================== */
  (function scope() {
    var cv = doc.getElementById('scope');
    var screen = doc.getElementById('scope-screen');
    var ctx = cv && cv.getContext && cv.getContext('2d');
    if (!ctx) return;
    var hud = {
      freq: doc.querySelector('[data-scope="freq"]'),
      amp: doc.querySelector('[data-scope="amp"]'),
      sweep: doc.querySelector('[data-scope="sweep"]')
    };
    var w = 0, h = 0, dpr = 1, t = 0, visible = true;
    var target = { x: 0.5, y: 0.5 }, cur = { x: 0.5, y: 0.5 }, active = false;

    function build() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = screen.clientWidth;
      h = screen.clientHeight;
      if (!w || !h) return;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
    }

    function wave(x, time, amp, f1, f2) {
      return Math.sin(x * f1 + time) * 0.62
        + Math.sin(x * f2 - time * 1.7) * 0.26
        + Math.sin(x * 31.4 + time * 2.4) * 0.12 * amp;
    }

    function frame(ts, dt) {
      if (!w || !h) { build(); return; }
      if (!visible) return;
      var k = reduced ? 0 : dt / 1000;
      t += k * 1.9;
      /* 指针缓动逼近：让调制是「跟手」而不是「跳变」 */
      cur.x += (target.x - cur.x) * Math.min(1, k * 4.5);
      cur.y += (target.y - cur.y) * Math.min(1, k * 4.5);

      var amp = 0.34 + cur.y * 0.5 + (active ? 0.1 : 0);
      var f1 = 7 + cur.x * 26;
      var f2 = 17 + cur.x * 44;

      /* 余辉：不清屏，压一层半透明底盘 */
      ctx.fillStyle = 'rgba(6,8,12,0.32)';
      ctx.fillRect(0, 0, w, h);

      /* 网格 */
      ctx.strokeStyle = 'rgba(148,168,190,0.075)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (var gx = 0; gx <= 10; gx++) {
        var px = Math.round(w * gx / 10) + 0.5;
        ctx.moveTo(px, 0); ctx.lineTo(px, h);
      }
      for (var gy = 0; gy <= 6; gy++) {
        var py = Math.round(h * gy / 6) + 0.5;
        ctx.moveTo(0, py); ctx.lineTo(w, py);
      }
      ctx.stroke();

      /* 中线 */
      ctx.strokeStyle = 'rgba(148,168,190,0.16)';
      ctx.beginPath();
      ctx.moveTo(0, Math.round(h / 2) + 0.5); ctx.lineTo(w, Math.round(h / 2) + 0.5);
      ctx.stroke();

      /* 波形 */
      var mid = h / 2, span = h * 0.36, step = 2;
      ctx.lineWidth = 1.6;
      ctx.lineJoin = 'round';
      ctx.strokeStyle = '#34f0a0';
      ctx.shadowColor = 'rgba(52,240,160,0.55)';
      ctx.shadowBlur = 12;
      ctx.beginPath();
      for (var x = 0; x <= w; x += step) {
        var u = x / w * 6.2832;
        var y = mid - wave(u, t, amp, f1, f2) * span * amp;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;

      /* 扫描头 */
      var hx = ((t * 0.16) % 1) * w;
      var hu = hx / w * 6.2832;
      var hy = mid - wave(hu, t, amp, f1, f2) * span * amp;
      ctx.strokeStyle = 'rgba(52,240,160,0.22)';
      ctx.beginPath(); ctx.moveTo(hx, 0); ctx.lineTo(hx, h); ctx.stroke();
      ctx.fillStyle = '#8fffce';
      ctx.beginPath(); ctx.arc(hx, hy, 2.2, 0, 6.2832); ctx.fill();

      if (hud.freq) hud.freq.textContent = (1 + cur.x * 3.4).toFixed(2);
      if (hud.amp) hud.amp.textContent = (0.3 + amp * 0.72).toFixed(2);
      if (hud.sweep) hud.sweep.textContent = (8.5 - cur.x * 5.5).toFixed(1);
    }

    build();
    window.addEventListener('resize', build, { passive: true });

    if (fine && screen) {
      screen.addEventListener('pointermove', function (e) {
        var r = screen.getBoundingClientRect();
        target.x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        target.y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
        active = true;
      });
      screen.addEventListener('pointerleave', function () {
        active = false;
        target.x = 0.5; target.y = 0.42;
      });
    }

    if ('IntersectionObserver' in window && screen) {
      new IntersectionObserver(function (en) {
        visible = en[0].isIntersecting;
      }, { threshold: 0 }).observe(screen);
    }

    if (reduced) { frame(0, 16); frame(0, 16); }
    else ticker.add(frame);
  })();

  /* ==========================================================
     6 · 显现编排
     ========================================================== */
  var REVEALS = [
    ['.hero-strip', 0], ['.hero-title', 60], ['.hero-lower', 220], ['.scroll-cue', 520],
    ['.sec-head', 0], ['.about-main > *', 0], ['.about-side', 120],
    ['.stack-card', 0], ['.proj-group', 0], ['.tl-item', 0], ['.sig-card', 0],
    ['.contact-inner', 0], ['.filters', 0]
  ];
  function armReveals() {
    if (!('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        en.target.classList.add('in-view');
        io.unobserve(en.target);
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -6% 0px' });

    REVEALS.forEach(function (set) {
      var els = doc.querySelectorAll(set[0]);
      [].forEach.call(els, function (el, i) {
        el.classList.add('reveal');
        el.style.setProperty('--d', (set[1] + Math.min(i, 6) * 90) + 'ms');
        io.observe(el);
      });
    });
    /* 轨迹线：进入视口才画 */
    var trace = doc.querySelector('.trace');
    if (trace) io.observe(trace);
  }

  /* ==========================================================
     7 · 导航：粘性态 / 当前区块 / 窄屏抽屉
     ========================================================== */
  (function nav() {
    var header = doc.querySelector('.site-header');
    var toggle = doc.querySelector('.nav-toggle');
    var list = doc.getElementById('nav-links');
    var links = [].slice.call(doc.querySelectorAll('.nav-links a'));
    var fill = doc.querySelector('.scroll-fill');
    var railRun = doc.querySelector('.rail-run');
    if (!header) return;

    function setNavOpen(open) {
      header.classList.toggle('nav-open', open);
      if (!toggle) return;
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.setAttribute('aria-label', (open ? '关闭' : '打开') + '导航菜单');
      var txt = toggle.querySelector('.nt-text');
      if (txt) txt.textContent = open ? 'CLOSE' : 'MENU';
    }

    if (toggle) {
      setNavOpen(false);
      toggle.addEventListener('click', function () {
        setNavOpen(!header.classList.contains('nav-open'));
      });
      links.forEach(function (a) {
        a.addEventListener('click', function () { setNavOpen(false); });
      });
      doc.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && header.classList.contains('nav-open')) {
          setNavOpen(false);
          toggle.focus();
        }
      });
      window.addEventListener('resize', function () {
        if (window.innerWidth > 760) setNavOpen(false);
      });
    }

    /* 当前区块：用 IntersectionObserver 而不是 offsetTop 轮询。
       顶栏与右侧标尺共用同一个 current，两边不会各说各话。 */
    var railLinks = [].slice.call(doc.querySelectorAll('.rail a'));
    var allLinks = links.concat(railLinks);
    if ('IntersectionObserver' in window) {
      var order = allLinks.map(function (a) {
        return doc.querySelector(a.getAttribute('href'));
      }).filter(Boolean);
      var current = null;
      var seen = {};
      var spy = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          seen[en.target.id] = en.isIntersecting;
          if (en.isIntersecting) current = '#' + en.target.id;
        });
        allLinks.forEach(function (a) {
          a.classList.toggle('is-active', a.getAttribute('href') === current);
        });
      }, { rootMargin: '-45% 0px -50% 0px', threshold: 0 });
      order.forEach(function (sec) { spy.observe(sec); });
    }

    /* 顶部进度 + header 状态 */
    var lastKnown = -1;
    function onScroll() {
      var y = window.scrollY || doc.documentElement.scrollTop || 0;
      if (y === lastKnown) return;
      lastKnown = y;
      header.classList.toggle('is-scrolled', y > 24);
      var max = doc.documentElement.scrollHeight - window.innerHeight;
      var p = max > 0 ? Math.min(1, y / max) : 0;
      if (fill) fill.style.width = (p * 100).toFixed(2) + '%';
      if (railRun) railRun.style.height = (p * 100).toFixed(2) + '%';
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    onScroll();
  })();

  /* ==========================================================
     8 · 自定义光标 + 磁吸按钮
     ========================================================== */
  (function cursor() {
    if (!fine || reduced) return;
    var el = doc.getElementById('cursor');
    if (!el) return;
    var dot = el.querySelector('.cur-dot');
    var ring = el.querySelector('.cur-ring');
    var label = el.querySelector('.cur-label');
    root.classList.add('has-cursor');

    var tx = window.innerWidth / 2, ty = window.innerHeight / 2;
    var rx = tx, ry = ty;

    doc.addEventListener('pointermove', function (e) {
      tx = e.clientX; ty = e.clientY;
      if (dot) dot.style.transform = 'translate3d(' + tx + 'px,' + ty + 'px,0)';
    }, { passive: true });

    function loop() {
      rx += (tx - rx) * 0.17;
      ry += (ty - ry) * 0.17;
      if (ring) ring.style.transform = 'translate3d(' + rx + 'px,' + ry + 'px,0)';
      if (label) label.style.transform = 'translate3d(' + (rx + 20) + 'px,' + (ry + 18) + 'px,0)';
    }
    ticker.add(loop);

    var HOT = 'a, button, .project-card, .stack-card, .chip, input, textarea';
    doc.addEventListener('pointerover', function (e) {
      var t = e.target && e.target.closest ? e.target.closest(HOT) : null;
      if (!t) return;
      el.classList.add('is-hot');
      if (label) {
        var card = t.closest('.project-card');
        label.textContent = card ? 'OPEN' : (t.closest('.mail-btn') ? 'COPY' : 'GO');
      }
    });
    doc.addEventListener('pointerout', function (e) {
      var t = e.target && e.target.closest ? e.target.closest(HOT) : null;
      if (t && !(e.relatedTarget && t.contains(e.relatedTarget))) el.classList.remove('is-hot');
    });

    /* 磁吸：按钮朝光标偏一点，松手弹回 */
    [].forEach.call(doc.querySelectorAll('[data-magnetic]'), function (b) {
      var STR = 0.28;
      b.addEventListener('pointermove', function (e) {
        var r = b.getBoundingClientRect();
        var dx = (e.clientX - (r.left + r.width / 2)) * STR;
        var dy = (e.clientY - (r.top + r.height / 2)) * STR;
        b.style.transform = 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px)';
      });
      b.addEventListener('pointerleave', function () { b.style.transform = ''; });
    });
  })();

  /* ==========================================================
     9 · 能力矩阵：电平 + SOLO
     ========================================================== */
  (function stack() {
    var strip = doc.querySelector('.stack-strip');
    if (!strip) return;
    var cards = [].slice.call(strip.querySelectorAll('.stack-card'));

    /* 电平表：进入视口后按通道给出不同的静态高度，再叠一点抖动 */
    var LEVELS = [0.92, 0.84, 0.78, 0.66];
    function setLevels(card, level, jitter) {
      var bars = card.querySelectorAll('.meter i');
      [].forEach.call(bars, function (bar, i) {
        var n = bars.length;
        var edge = 1 - Math.abs((i / (n - 1)) - 0.5) * 1.1;
        var v = level * (0.42 + edge * 0.58);
        if (jitter) v *= 0.82 + Math.random() * 0.34;
        bar.style.height = Math.max(12, Math.min(100, v * 100)).toFixed(0) + '%';
      });
    }
    cards.forEach(function (card, ci) {
      setLevels(card, LEVELS[ci % LEVELS.length], false);
      var btn = card.querySelector('.ch-solo');
      if (!btn) return;
      btn.addEventListener('click', function () {
        var on = btn.getAttribute('aria-pressed') === 'true';
        cards.forEach(function (c) {
          c.classList.remove('is-solo', 'is-muted');
          var b = c.querySelector('.ch-solo');
          if (b) b.setAttribute('aria-pressed', 'false');
        });
        if (!on) {
          card.classList.add('is-solo');
          btn.setAttribute('aria-pressed', 'true');
          cards.forEach(function (c) { if (c !== card) c.classList.add('is-muted'); });
        }
      });
    });

    /* 光标经过时给一点活着的抖动 */
    if (!reduced) {
      cards.forEach(function (card, ci) {
        card.addEventListener('pointerenter', function () {
          var lvl = LEVELS[ci % LEVELS.length];
          var n = 0;
          var id = setInterval(function () {
            setLevels(card, lvl, true);
            if (++n > 14) clearInterval(id);
          }, 90);
        });
      });
    }
  })();

  /* ==========================================================
     10 · 项目分区筛选
     ========================================================== */
  (function filters() {
    var chips = [].slice.call(doc.querySelectorAll('.chip[data-filter]'));
    var groups = [].slice.call(doc.querySelectorAll('.proj-group[data-group]'));
    if (!chips.length || !groups.length) return;
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        var f = chip.getAttribute('data-filter');
        chips.forEach(function (c) {
          var on = c === chip;
          c.classList.toggle('is-on', on);
          c.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        groups.forEach(function (g) {
          var show = f === 'all' || g.getAttribute('data-group') === f;
          g.classList.toggle('is-hidden', !show);
          if (show) g.classList.add('in-view');
        });
      });
    });
  })();

  /* ==========================================================
     11 · 数字滚动
     ========================================================== */
  (function counters() {
    var els = [].slice.call(doc.querySelectorAll('[data-count]'));
    if (!els.length) return;
    if (!('IntersectionObserver' in window) || reduced) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        io.unobserve(en.target);
        var el = en.target;
        var to = parseInt(el.getAttribute('data-count'), 10);
        if (isNaN(to)) return;
        var t0 = null, DUR = 900;
        function step(ts) {
          if (t0 === null) t0 = ts;
          var p = Math.min(1, (ts - t0) / DUR);
          var e = 1 - Math.pow(1 - p, 3);
          el.textContent = String(Math.round(to * e)).padStart(2, '0');
          if (p < 1) requestAnimationFrame(step);
        }
        el.textContent = '00';
        requestAnimationFrame(step);
      });
    }, { threshold: 0.4 });
    els.forEach(function (el) { io.observe(el); });
  })();

  /* ==========================================================
     12 · 复制邮箱
     ========================================================== */
  (function copyMail() {
    var btn = doc.querySelector('.mail-btn[data-copy]');
    if (!btn) return;
    var hint = btn.querySelector('.mail-hint');
    var text = btn.getAttribute('data-copy');
    var timer = null;
    btn.addEventListener('click', function () {
      function ok() {
        if (!hint) return;
        hint.textContent = '已复制 · COPIED';
        btn.classList.add('is-copied');
        clearTimeout(timer);
        timer = setTimeout(function () {
          hint.textContent = '点击复制';
          btn.classList.remove('is-copied');
        }, 2200);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(ok, fallback);
      } else { fallback(); }
      function fallback() {
        var ta = doc.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.cssText = 'position:fixed;top:-9999px;opacity:0';
        doc.body.appendChild(ta);
        ta.select();
        var done = false;
        try { done = doc.execCommand('copy'); } catch (e) {}
        doc.body.removeChild(ta);
        if (done) ok();
      }
    });
  })();

  /* ==========================================================
     13 · 页脚年份 + 启动
     ========================================================== */
  var yearEl = doc.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  startBoot(function () {
    armReveals();
    [].forEach.call(doc.querySelectorAll('[data-decode]'), function (el, i) {
      decode(el, i * 130);
    });
  });
})();
