/* ============================================================
   RevolutionLA · SIGMA INSTRUMENT
   仪器的前面板：开机校准 → 解码 → 实时读数 → 滚动编排
   ------------------------------------------------------------
   三条硬规矩
     1. 任何动画都要给 prefers-reduced-motion 一条静态出路（不是「删掉」，
        而是「渲染成最终态」）。
     2. 所有绘制走一个 rAF 循环：页面不可见就停表，常驻背景静置四秒后降半速——
        任何输入立刻回到满帧。
     3. JS 缺席时页面必须完整可读：装饰件（光标/开机）自行隐身。
     4. 系统偏好可以在页面开着的时候改，所以「减少动效」是活的监听，不是加载时的一次快照。
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
      /* 时间戳理论上单调递增，但一旦倒了一次，负的 dt 会把所有阻尼变成放大 */
      var dt = Math.max(1, Math.min(48, ts - last || 16));
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
    if (doc.hidden) { ticker.stop(); return; }
    ticker.start();
    /* 后台标签页的 setInterval 会被节流到约一分钟一次：
       这块表写的是「成都此刻」，回到前台必须立刻补一跳，不能带着旧读数示人。 */
    tickClock();
  });

  /* 「减少动效」是访客可以在页面开着的时候改的系统设置，所以这条承诺不能只在
     加载那一刻成立。各动画回路把自己的重算函数挂到 motionHooks 上，这里统一通知。 */
  var motionHooks = [];
  var mqReduce = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  if (mqReduce && mqReduce.addEventListener) {
    mqReduce.addEventListener('change', function () {
      reduced = mqReduce.matches;
      for (var i = 0; i < motionHooks.length; i++) motionHooks[i](reduced);
    });
  }

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
     4 · 信号粒子场（全站常驻 · 指针斥流 / 点击冲击波 / 滚动充能）
     整站只有一张背景 canvas、一条 rAF 回路（挂在 ticker 上，
     后台标签页自动停表）。粒子读作面板里的「载流子」：
     静止时沿流场缓慢漂移，被指针或冲击波扰动就带上电荷 q，
     q 用颜色（蓝白→信号绿→琥珀余辉）、拖尾和一层廉价光晕表达。
     不用 shadowBlur、不用全屏滤镜——那是掉帧的头号来源。
     ========================================================== */
  (function signalField() {
    var cv = doc.getElementById('field');
    var ctx = cv && cv.getContext && cv.getContext('2d');
    if (!ctx) return;

    var coarse = !fine;                 /* 触摸设备没有 hover：只留点击脉冲 */
    var w = 0, h = 0, dpr = 1;
    var parts = [], waves = [];
    var target = 0, tuned = 0;
    var T = 0, ema = 16;
    var P = { x: -1e4, y: -1e4, vx: 0, vy: 0, on: false };
    var kick = 0, lastY = 0;
    /* 静置降速：4 秒没人碰它，就隔帧再算。漂移本身很慢，半速看不出来，
       但一整块全屏合成层少画一半的帧——这是常驻背景唯一实在的省电法。
       任何指针、点击、滚动、缩放输入都把帧率立刻拉回来。 */
    var lastAct = 0, lastMove = -1e4, acc = 0;

    function mk() {
      var depth = [0.35, 0.7, 1.15][Math.floor(Math.random() * 3)];
      return {
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.05, vy: (Math.random() - 0.5) * 0.05,
        depth: depth,
        r: (0.45 + depth * 0.9) * (0.6 + Math.random() * 0.8),
        a: (0.05 + depth * 0.26) * (0.55 + Math.random() * 0.6),
        ph: Math.random() * 6.2832,
        sp: 0.0009 + Math.random() * 0.0026,
        q: 0
      };
    }
    function fit(n) {
      while (parts.length < n) parts.push(mk());
      if (parts.length > n) parts.length = n;
    }
    /* 粒子数按视口面积给，触摸端和小核机器再砍一刀 */
    function budget() {
      var n = Math.round((w * h) / 13000);
      n = Math.max(58, Math.min(n, coarse ? 88 : 172));
      if ((navigator.hardwareConcurrency || 8) <= 4) n = Math.round(n * 0.7);
      return n;
    }
    function build() {
      var nw = doc.documentElement.clientWidth || window.innerWidth;
      var nh = doc.documentElement.clientHeight || window.innerHeight;
      if (!nw || !nh) return;              /* 0x0 会把所有粒子焊死在左上角 */
      w = nw; h = nh;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      /* 给 canvas.width 赋同一个值同样会清空位图，所以尺寸真变了才动它 */
      var bw = Math.round(w * dpr), bh = Math.round(h * dpr);
      if (cv.width !== bw || cv.height !== bh) {
        cv.width = bw; cv.height = bh;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      target = budget();
      fit(target);
      /* 视口变小时被留在框外的粒子重新撒开，而不是整块挤到边缘排队 */
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].x > w || parts[i].y > h) {
          parts[i].x = Math.random() * w;
          parts[i].y = Math.random() * h;
        }
      }
    }
    function pulse(x, y) {
      if (waves.length > 3) waves.shift();
      waves.push({ x: x, y: y, age: 0, r: 0, life: 1, dur: 900 });
    }

    function step(ts, dt) {
      acc += dt;
      if (ts - lastAct > 4000 && acc < 33) return true;   /* 静置：这一帧不画 */
      dt = Math.min(48, acc);
      acc = 0;
      var k = dt / 16.6667;                       /* 归一化步长：掉帧时动作不失真 */
      T += dt;
      ctx.clearRect(0, 0, w, h);
      if (P.on && ts - lastMove > 900) P.on = false;  /* 光标停住不等于一直在搅动 */

      for (var wi = waves.length - 1; wi >= 0; wi--) {
        var W0 = waves[wi];
        W0.age += dt;
        W0.r = W0.age * 0.62;
        W0.life = W0.age < W0.dur ? 1 - W0.age / W0.dur : 0;
        if (!W0.life) waves.splice(wi, 1);
      }

      /* 指针速度只用于算「搅动」强度：P.vx 是上一帧的位移，除以 k 才是
         「每 16.7ms 的位移」，否则 30fps 下搅动会强一倍。 */
      var spd = Math.min(1, (Math.abs(P.vx) + Math.abs(P.vy)) / (46 * k));
      var swirl = spd * 0.5;
      P.vx *= Math.pow(0.72, k); P.vy *= Math.pow(0.72, k);
      kick *= Math.pow(0.9, k);

      var R = 168, RI = 268, i, p;
      for (i = 0; i < parts.length; i++) {
        p = parts[i];

        /* 1 · 流场：两条不同频率的正弦叠加，慢且没有明显周期 */
        var ang = (Math.sin(p.y * 0.0042 + T * 0.00021) + Math.cos(p.x * 0.0037 - T * 0.00017)) * 1.9;
        p.vx += Math.cos(ang) * 0.0032 * p.depth * k;
        p.vy += Math.sin(ang) * 0.0032 * p.depth * k;

        /* 2 · 指针：内圈斥开并绕切向，外圈轻微吸回来，形成一圈悬尘 */
        if (P.on) {
          var dx = p.x - P.x, dy = p.y - P.y;
          var d2 = dx * dx + dy * dy;
          if (d2 < RI * RI) {
            var d = Math.sqrt(d2) || 1, ux = dx / d, uy = dy / d;
            if (d < R) {
              var f = 1 - d / R; f *= f;
              p.vx += (ux * 0.6 - uy * swirl) * f * k;
              p.vy += (uy * 0.6 + ux * swirl) * f * k;
              p.q += f * 0.11 * k;
            } else {
              var g = (1 - (d - R) / (RI - R)) * 0.05;
              p.vx -= ux * g * k; p.vy -= uy * g * k;
              p.q += g * 0.4 * k;
            }
          }
        }

        /* 3 · 冲击波：落在环带里的粒子被径向外推并点亮 */
        for (var wn = 0; wn < waves.length; wn++) {
          var W = waves[wn];
          var wx = p.x - W.x, wy = p.y - W.y;
          var wd = Math.sqrt(wx * wx + wy * wy) || 1;
          var band = Math.abs(wd - W.r);
          if (band < 46) {
            var wf = (1 - band / 46) * W.life;
            p.vx += (wx / wd) * 1.15 * wf * k;
            p.vy += (wy / wd) * 1.15 * wf * k;
            p.q += wf * 0.45 * k;
          }
        }

        /* 4 · 滚动：整场沿滚动反方向被拖一下，速度越快充能越多。
           kick 已经是「这一帧滚了多少」，再乘 k 就变成 ∝dt²，低帧率会过冲。 */
        p.vy += kick * p.depth;
        p.q += Math.min(0.02, Math.abs(kick) * 0.05);

        /* 5 · 阻尼、积分、环绕 */
        var damp = Math.pow(0.985, k);
        p.vx *= damp; p.vy *= damp;
        p.x += p.vx * k * 2.2; p.y += p.vy * k * 2.2;
        if (p.x < -8) p.x = w + 7; else if (p.x > w + 8) p.x = -7;
        if (p.y < -8) p.y = h + 7; else if (p.y > h + 8) p.y = -7;
        p.q *= Math.pow(0.945, k);
        if (p.q > 1) p.q = 1;
        p.ph += p.sp * dt;

        draw(p);
      }

      for (i = 0; i < waves.length; i++) {
        var RW = waves[i];
        ctx.strokeStyle = 'rgba(52,240,160,' + (RW.life * 0.15).toFixed(3) + ')';
        ctx.lineWidth = 1 + RW.life * 1.3;
        ctx.beginPath();
        ctx.arc(RW.x, RW.y, RW.r, 0, 6.2832);
        ctx.stroke();
      }

      /* 6 · 自适应画质：帧时间持续偏高就减粒子，宽裕再长回去 */
      ema += (dt - ema) * 0.05;
      if (++tuned > 120) {
        tuned = 0;
        if (ema > 26 && parts.length > 46) { fit(Math.max(46, Math.round(parts.length * 0.8))); ema = 16; }
        else if (ema < 13.5 && parts.length < target) fit(Math.min(target, parts.length + 8));
      }
      return true;
    }

    function draw(p) {
      var tw = 0.66 + 0.34 * Math.sin(p.ph);
      var a = Math.min(0.95, p.a * tw + p.q * 0.5);
      var m = Math.min(1, p.q * 1.6);
      var cr = 226 + (52 - 226) * m, cg = 236 + (240 - 236) * m, cb = 246 + (160 - 246) * m;
      if (p.q > 0.55) {                       /* 电荷顶格时往琥珀偏，像被烧了一下 */
        cr += (242 - cr) * 0.4; cg += (178 - cg) * 0.4; cb += (76 - cb) * 0.4;
      }
      var col = 'rgba(' + (cr | 0) + ',' + (cg | 0) + ',' + (cb | 0) + ',' + a.toFixed(3) + ')';
      var spd = Math.abs(p.vx) + Math.abs(p.vy);
      if (spd > 0.2 || p.q > 0.22) {          /* 动起来才拖尾，静止时是干净的点 */
        ctx.strokeStyle = col;
        ctx.lineWidth = p.r * 0.9;
        ctx.beginPath();
        ctx.moveTo(p.x - p.vx * 7, p.y - p.vy * 7);
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
      } else {
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, 6.2832);
        ctx.fill();
      }
      if (p.q > 0.18) {
        ctx.fillStyle = 'rgba(52,240,160,' + (p.q * 0.085).toFixed(3) + ')';
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r * 4.2, 0, 6.2832);
        ctx.fill();
      }
    }

    function still() {
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < parts.length; i++) draw(parts[i]);
    }

    build();

    /* rAF 的 ts 和 performance.now() 同一个时间原点，事件 timeStamp 也是 */
    function stamp(e) { return (e && e.timeStamp) || performance.now(); }

    var rt = null;
    window.addEventListener('resize', function () {
      /* 手机地址栏收起会连发 resize，每次都重建位图就是白烧一次显存 */
      if (rt) return;
      rt = setTimeout(function () {
        rt = null; build(); lastAct = performance.now(); if (reduced) still();
      }, 160);
    }, { passive: true });

    window.addEventListener('pointermove', function (e) {
      if (reduced || coarse) return;        /* 触屏没有悬停：那一下不该被当成「移动扰动」 */
      var t = stamp(e);
      /* 第一次出现（或停住后又动）只记位置：否则 P.vx 会吃进一个上万的假位移，
         搅动强度直接顶格，画面像被抽了一下。 */
      if (P.on && t - lastMove < 900) {
        P.vx += e.clientX - P.x; P.vy += e.clientY - P.y;
      } else {
        P.vx = P.vy = 0;
      }
      P.x = e.clientX; P.y = e.clientY; P.on = true;
      lastMove = t; lastAct = t;
    }, { passive: true });
    window.addEventListener('pointerout', function (e) {
      if (!e.relatedTarget) P.on = false;
    }, { passive: true });
    window.addEventListener('pointerdown', function (e) {
      if (reduced) return;
      pulse(e.clientX, e.clientY);          /* 触摸端唯一的互动入口，点击必须有反馈 */
      lastAct = stamp(e);
    }, { passive: true });
    lastY = window.scrollY || 0;
    window.addEventListener('scroll', function () {
      var y = window.scrollY || 0;
      kick = Math.max(-0.5, Math.min(0.5, -(y - lastY) * 0.012));
      lastY = y;
      if (!reduced) lastAct = performance.now();
    }, { passive: true });

    /* 让访客知道这一层是活的。竖排在那道右侧沟槽里（CSS 只在 ≥1024 显示），不压正文；
       触屏上「移动以扰动」是假话，所以只在精确指针下挂出来。
       碰一次、滚出首屏、12 秒无人理都退场，并且把自己的监听和计时器一并摘掉。 */
    var hint = doc.getElementById('field-hint');
    var hintTimer = null;
    function hintScroll() {
      if ((window.scrollY || 0) > window.innerHeight * 0.6) retireHint();
    }
    function retireHint() {
      if (!hint || !hint.classList.contains('is-armed')) return;
      hint.classList.add('is-off');
      window.removeEventListener('pointerdown', retireHint);
      window.removeEventListener('scroll', hintScroll);
      if (hintTimer) { clearTimeout(hintTimer); hintTimer = null; }
    }
    function armHint() {
      if (!hint || !fine || innerWidth < 1024 || reduced) return;
      if (hint.classList.contains('is-off')) return;   /* 只说一次，不追着人重复 */
      hint.classList.add('is-armed');
      window.addEventListener('pointerdown', retireHint, { passive: true });
      window.addEventListener('scroll', hintScroll, { passive: true });
      hintTimer = setTimeout(retireHint, 12000);
    }

    var running = false;
    function setMotion(on) {
      if (on === running) return;
      running = on;
      if (on) { acc = 0; lastAct = performance.now(); ticker.add(step); }
      else { ticker.remove(step); still(); }
    }

    if (reduced) still();                     /* 减少动效：一屏静止微尘，一帧都不画 */
    else { setMotion(true); armHint(); }

    motionHooks.push(function (off) { setMotion(!off); if (off) retireHint(); else armHint(); });

    /* 指针类型同样可以中途变（触屏笔记本插上鼠标）：档位和提示跟着重算 */
    var mqFine = window.matchMedia ? window.matchMedia('(hover: hover) and (pointer: fine)') : null;
    if (mqFine && mqFine.addEventListener) {
      mqFine.addEventListener('change', function () {
        fine = mqFine.matches; coarse = !fine;
        P.on = false;
        build();
        if (fine) armHint(); else retireHint();
        if (reduced) still();
      });
    }
  })();

  /* ==========================================================
     5 · 显现编排
     ========================================================== */
  var REVEALS = [
    ['.hero-strip', 0], ['.hero-title', 60], ['.hero-lower', 220],
    ['.sec-head', 0], ['.about-main > *', 0], ['.about-side', 120],
    ['.stack-card', 0], ['.proj-group', 0], ['.proj-rest .project-card', 0],
    ['.tl-item', 0], ['.sig-card', 0],
    ['.contact-inner', 0], ['.proj-rest-head', 0]
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
     6 · 导航：粘性态 / 当前区块 / 窄屏抽屉
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
     7 · 自定义光标 + 磁吸按钮
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
    /* 关掉动效时连自定义游标一起交还：光标必须回到系统那只，而不是只停住环 */
    motionHooks.push(function (off) {
      if (off) { ticker.remove(loop); root.classList.remove('has-cursor'); }
      else { root.classList.add('has-cursor'); ticker.add(loop); }
    });

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
     8 · 能力矩阵：电平 + SOLO
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
     10 · 分类筛选（卡片级）
     ========================================================== */
  (function filters() {
    var chips = [].slice.call(doc.querySelectorAll('.chip[data-filter]'));
    /* 范围限定在 .proj-rest 之内：主打卡（.flagship）不参与筛选，
       不然点一下「AI · 音乐」会把最重要的那张卡一起藏掉。 */
    var cards = [].slice.call(doc.querySelectorAll('.proj-rest .project-card[data-cat]'));
    var status = doc.querySelector('.filt-status b');
    if (!chips.length || !cards.length) return;
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        var f = chip.getAttribute('data-filter');
        var shown = 0;
        chips.forEach(function (c) {
          var on = c === chip;
          c.classList.toggle('is-on', on);
          c.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        cards.forEach(function (card) {
          var show = f === 'all' || card.getAttribute('data-cat') === f;
          card.classList.toggle('is-hidden', !show);
          /* 被筛出来的卡可能从没进过视口，reveal 还停在 opacity:0：
             既然现在要给它看，就直接落到终态。 */
          if (show) { shown++; card.classList.add('in-view'); }
        });
        /* 筛选改了屏幕上还剩几张，这件事必须说出来，而不只是「看起来变了」 */
        if (status) {
          var cnt = chip.querySelector('b');
          var label = cnt ? chip.textContent.replace(cnt.textContent, '').trim() : f;
          status.textContent = f === 'all'
            ? '全部 ' + pad(shown) + ' 个'
            : label + ' · ' + pad(shown) + ' 个';
        }
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
