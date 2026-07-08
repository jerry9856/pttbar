"""webui.py — PTTBar 主視窗的 HTML/CSS/JS。

設計語言：iOS（參考 Music app）× PTT 終端機風味。
- 無 emoji：導覽/裝飾一律用 SF Symbols 風格的 inline SVG（stroke=currentColor）。
- 毛玻璃：gui.py 成功設定透明 chrome 時會把 <html> 加上 class="glass"，
  頁面改用半透明表面讓 NSVisualEffectView 的模糊透出；否則用純色（安全退路）。
- PTT 風味：品牌字標（等寬、終端底綠字）、推文內容等寬字型、推/噓/→ 三色。

與 Python 溝通：
  JS → Py：window.webkit.messageHandlers.py.postMessage({cmd, ...})
  Py → JS：window.__update(state) / window.__history(rows)
所有使用者內容一律 textContent 塞入（防注入）。
"""

HTML = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<style>
  :root{
    --bg:#f2f2f7; --side:#ececf1; --card:#ffffff; --text:#1d1d1f; --sub:#86868b;
    --sep:rgba(60,60,67,.10); --hair:rgba(60,60,67,.14);
    --accent:#fa2d48; --accent-soft:rgba(250,45,72,.10);
    --push:#28a745; --push-soft:rgba(40,167,69,.13);
    --boo:#e0362c;  --boo-soft:rgba(224,54,44,.12);
    --arrow:#a07800;--arrow-soft:rgba(255,204,0,.20);
    --hover:rgba(0,0,0,.035); --press:rgba(0,0,0,.07);
    --term:#0b0f0c; --term-green:#4ade80;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#1c1c1e; --side:#232326; --card:#2a2a2d; --text:#f5f5f7; --sub:#98989f;
      --sep:rgba(84,84,88,.40); --hair:rgba(84,84,88,.55);
      --accent:#ff4f63; --accent-soft:rgba(255,79,99,.16);
      --push:#30d158; --push-soft:rgba(48,209,88,.15);
      --boo:#ff453a;  --boo-soft:rgba(255,69,58,.15);
      --arrow:#ffd60a;--arrow-soft:rgba(255,214,10,.14);
      --hover:rgba(255,255,255,.05); --press:rgba(255,255,255,.10);
    }
  }
  /* 毛玻璃模式：表面改半透明，讓視窗模糊透出 */
  html.glass{--side:transparent}
  html.glass body{background:transparent}
  @media (prefers-color-scheme: light){
    html.glass{--bg:transparent;--card:rgba(255,255,255,.62)}
    html.glass aside{background:rgba(246,246,248,.30)}
  }
  @media (prefers-color-scheme: dark){
    html.glass{--bg:transparent;--card:rgba(44,44,48,.55)}
    html.glass aside{background:rgba(28,28,30,.25)}
  }
  *{margin:0;padding:0;box-sizing:border-box;-webkit-user-select:none;user-select:none}
  html,body{height:100%}
  body{
    font-family:-apple-system,"SF Pro Text","PingFang TC",sans-serif;
    background:var(--bg); color:var(--text); font-size:13px;
    display:flex; overflow:hidden;
  }
  svg.ic{width:17px;height:17px;flex:none;stroke:currentColor;stroke-width:1.7;
    fill:none;stroke-linecap:round;stroke-linejoin:round}
  /* ---------- 側欄 ---------- */
  aside{
    width:198px;min-width:198px;padding:46px 10px 12px;   /* 上方讓出紅綠燈 */
    display:flex;flex-direction:column;gap:2px;background:var(--side);
    border-right:1px solid var(--hair);
  }
  .brand{display:flex;align-items:baseline;gap:5px;padding:0 12px 16px}
  .brand .logo{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:13px;
    font-weight:700;color:var(--term-green);background:var(--term);
    border-radius:6px;padding:2px 7px;letter-spacing:.5px}
  .brand .name{font-size:19px;font-weight:800;letter-spacing:-.3px}
  .brand small{font-size:10.5px;color:var(--sub);font-weight:500;margin-left:auto}
  .nav{
    display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:9px;
    font-size:13.5px;font-weight:500;color:var(--text);cursor:default;
  }
  .nav:hover{background:var(--hover)}
  .nav.active{background:var(--accent);color:#fff;font-weight:600}
  .spacer{flex:1}
  .conn{padding:8px 12px;font-size:11px;color:var(--sub);line-height:1.5;
    font-family:ui-monospace,"SF Mono",Menlo,monospace}
  /* ---------- 主區 ---------- */
  main{flex:1;overflow-y:auto;padding:44px 26px 34px}
  .view{display:none;max-width:700px;margin:0 auto}
  .view.show{display:block}
  h1{font-size:26px;font-weight:800;letter-spacing:-.5px;margin-bottom:16px}
  .card{
    background:var(--card);border-radius:16px;margin-bottom:16px;overflow:hidden;
    box-shadow:0 1px 2px rgba(0,0,0,.05),0 4px 16px rgba(0,0,0,.04),0 0 0 .5px var(--hair);
  }
  .card .pad{padding:16px 18px}
  .card h3{font-size:11px;font-weight:600;color:var(--sub);text-transform:uppercase;
    letter-spacing:.6px;padding:13px 18px 5px;display:flex;align-items:center;gap:6px}
  .card h3 .right{margin-left:auto;color:var(--accent);text-transform:none;
    letter-spacing:0;font-weight:600;font-size:12px}
  .card h3 .right:hover{opacity:.7}
  /* 列 */
  .row{display:flex;align-items:center;gap:11px;padding:11px 18px;position:relative;min-height:44px}
  .row+.row::before{content:"";position:absolute;top:0;left:18px;right:0;height:.5px;background:var(--sep)}
  .row.click{cursor:default}
  .row.click:hover{background:var(--hover)}
  .row.click:active{background:var(--press)}
  .row .grow{flex:1;min-width:0}
  .row .t{font-size:13.5px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .row .s{font-size:11.5px;color:var(--sub);margin-top:1.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .chev{color:var(--sub);font-size:15px;font-weight:600;font-family:-apple-system}
  /* 徽章 */
  .badge{min-width:27px;height:21px;border-radius:6.5px;display:inline-flex;align-items:center;
    justify-content:center;font-size:11px;font-weight:700;padding:0 5px;flex:none;
    font-variant-numeric:tabular-nums}
  .b-push{background:var(--push-soft);color:var(--push)}
  .b-boo{background:var(--boo-soft);color:var(--boo)}
  .b-arrow{background:var(--arrow-soft);color:var(--arrow)}
  .b-num{background:var(--accent-soft);color:var(--accent)}
  .b-n0{background:transparent;color:var(--sub)}
  .b-n1{background:var(--push-soft);color:var(--push)}
  .b-n2{background:var(--arrow-soft);color:var(--arrow)}
  .b-hot{background:var(--boo);color:#fff}
  .b-x{background:rgba(120,120,128,.18);color:var(--sub)}
  /* 按鈕 */
  .btn{
    display:inline-flex;align-items:center;justify-content:center;gap:6px;
    border:none;border-radius:10px;padding:8px 15px;font-size:13px;font-weight:600;
    font-family:inherit;background:var(--accent-soft);color:var(--accent);cursor:default;
  }
  .btn:hover{filter:brightness(1.05)} .btn:active{transform:scale(.97);filter:brightness(.9)}
  .btn.primary{background:var(--accent);color:#fff}
  .btn.danger{background:var(--boo-soft);color:var(--boo)}
  .btn.ghost{background:transparent;color:var(--accent)}
  .btnrow{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}
  /* 輸入 */
  input[type=text]{
    -webkit-user-select:auto;user-select:auto;
    width:100%;border:none;background:rgba(120,120,128,.12);border-radius:10px;
    padding:9px 13px;font-size:13px;font-family:inherit;color:var(--text);outline:none;
  }
  input[type=text]:focus{box-shadow:0 0 0 3px var(--accent-soft)}
  /* 滑桿 */
  input[type=range]{-webkit-appearance:none;flex:1;height:4px;border-radius:2px;
    background:rgba(120,120,128,.28);outline:none}
  input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:21px;height:21px;
    border-radius:50%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.3);cursor:default}
  .sliderval{min-width:64px;text-align:right;font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums}
  /* 開關 */
  .switch{width:44px;height:26px;border-radius:13px;background:rgba(120,120,128,.28);
    position:relative;flex:none;transition:background .18s}
  .switch.on{background:var(--push)}
  .switch::after{content:"";position:absolute;top:2px;left:2px;width:22px;height:22px;
    border-radius:50%;background:#fff;box-shadow:0 1.5px 4px rgba(0,0,0,.25);transition:left .18s}
  .switch.on::after{left:20px}
  /* 目前追蹤卡 */
  .now-title{font-size:16.5px;font-weight:700;line-height:1.35;-webkit-user-select:text;user-select:text}
  .now-meta{display:flex;align-items:center;gap:9px;margin-top:8px;flex-wrap:wrap}
  .chip{font-size:11px;font-weight:600;background:var(--accent-soft);color:var(--accent);
    border-radius:6px;padding:2.5px 8px;font-family:ui-monospace,"SF Mono",Menlo,monospace}
  .statusline{font-size:12px;color:var(--sub)}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--push);margin-right:5px}
  .dot.warn{background:#ff9f0a} .dot.off{background:var(--sub)}
  /* 推文 feed（PTT 風味：等寬字） */
  .feed .row{padding:9px 18px;min-height:0}
  .feed .row.kw{background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)}
  .c-line{font-size:12.5px;line-height:1.45;-webkit-user-select:text;user-select:text;
    word-break:break-all;font-family:ui-monospace,"SF Mono",Menlo,"PingFang TC",monospace}
  .c-line .au{font-weight:700;margin-right:7px}
  .c-time{font-size:10.5px;color:var(--sub);flex:none;font-variant-numeric:tabular-nums;
    font-family:ui-monospace,"SF Mono",Menlo,monospace}
  .empty{padding:28px 18px;text-align:center;color:var(--sub);font-size:12.5px}
  .note{font-size:11px;color:var(--sub);font-weight:500;text-transform:none;letter-spacing:0;margin-left:auto}
  /* 統計 */
  .statrow{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
  .stat{border-radius:16px;padding:13px 15px;
    box-shadow:0 1px 2px rgba(0,0,0,.05),0 0 0 .5px var(--hair);background:var(--card)}
  .stat .n{font-size:23px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
  .stat .l{font-size:11px;font-weight:600;margin-top:2px;color:var(--sub)}
  .stat.s-push .n{color:var(--push)} .stat.s-arrow .n{color:var(--arrow)}
  .stat.s-boo .n{color:var(--boo)} .stat.s-rate .n{color:var(--accent)}
  .spark{display:flex;align-items:flex-end;gap:4px;height:54px}
  .spark .bar{flex:1;border-radius:3px 3px 1.5px 1.5px;
    background:linear-gradient(to top,var(--accent),rgba(250,45,72,.55));
    min-height:2px;transition:height .3s}
  .toprow-bar{height:5px;border-radius:2.5px;background:var(--accent-soft);overflow:hidden;margin-top:5px}
  .toprow-bar>div{height:100%;background:var(--accent);border-radius:2.5px}
  /* 看板列表切換 */
  .backbar{display:flex;align-items:center;gap:5px;margin-bottom:12px;color:var(--accent);
    font-size:13.5px;font-weight:600}
  .backbar:hover{opacity:.75}
  .loadmore{padding:12px 18px;text-align:center;color:var(--accent);font-weight:600;
    font-size:13px;border-top:.5px solid var(--sep)}
  .loadmore:hover{background:var(--hover)} .loadmore:active{background:var(--press)}
  .loadmore.loading{color:var(--sub)}
  /* 分段控制（歷史型別篩選，iOS segmented） */
  .searchbar{margin-bottom:12px}
  .seg{display:inline-flex;background:rgba(120,120,128,.16);border-radius:9px;
    padding:2px;margin-top:10px;gap:2px}
  .seg .seg-item{font-size:12px;font-weight:600;padding:4.5px 13px;border-radius:7px;
    color:var(--text)}
  .seg .seg-item.active{background:var(--card);box-shadow:0 1px 3px rgba(0,0,0,.12)}
  html.glass .seg .seg-item.active{background:rgba(255,255,255,.85);color:#1d1d1f}
  /* 主題色點 */
  .dots{display:flex;gap:5px}
  .cdot{width:16px;height:16px;border-radius:50%;box-shadow:inset 0 0 0 .5px var(--hair)}
  .check{color:var(--accent);font-weight:700;font-size:15px;width:18px;text-align:center}
  /* toast */
  #toast{
    position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(20px);
    background:var(--card);color:var(--text);padding:9px 18px;border-radius:20px;
    font-size:12.5px;font-weight:500;
    box-shadow:0 6px 24px rgba(0,0,0,.22),0 0 0 .5px var(--hair);
    opacity:0;transition:all .25s;pointer-events:none;max-width:70%;backdrop-filter:blur(20px);
  }
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  .hidden{display:none !important}
  ::-webkit-scrollbar{width:8px} ::-webkit-scrollbar-thumb{background:var(--sep);border-radius:4px}
</style>
</head>
<body>
<aside>
  <div class="brand"><span class="logo">PTT</span><span class="name">Bar</span><small id="acct">未登入</small></div>
  <div class="nav active" data-v="now">
    <svg class="ic" viewBox="0 0 20 20"><circle cx="10" cy="10" r="1.6" fill="currentColor" stroke="none"/><path d="M6.5 13.5a5 5 0 0 1 0-7M13.5 6.5a5 5 0 0 1 0 7"/><path d="M4.2 15.8a8.2 8.2 0 0 1 0-11.6M15.8 4.2a8.2 8.2 0 0 1 0 11.6"/></svg>
    目前追蹤</div>
  <div class="nav" data-v="boards">
    <svg class="ic" viewBox="0 0 20 20"><rect x="3" y="3.5" width="14" height="13" rx="2.5"/><path d="M7 7.5h6M7 10h6M7 12.5h3.5"/></svg>
    看板</div>
  <div class="nav" data-v="history">
    <svg class="ic" viewBox="0 0 20 20"><circle cx="10" cy="10" r="7"/><path d="M10 6.2V10l2.6 1.8"/></svg>
    歷史</div>
  <div class="nav" data-v="settings">
    <svg class="ic" viewBox="0 0 20 20"><path d="M4 6.5h7M14.5 6.5H16M4 13.5h2M9.5 13.5H16"/><circle cx="12.7" cy="6.5" r="1.9"/><circle cx="7.3" cy="13.5" r="1.9"/></svg>
    設定</div>
  <div class="spacer"></div>
  <div class="conn" id="connline"></div>
</aside>
<main>
  <!-- ============ 追蹤 ============ -->
  <div class="view show" id="v-now">
    <h1>目前追蹤</h1>
    <div class="card"><div class="pad">
      <div class="now-title" id="a-title">尚未追蹤文章</div>
      <div class="now-meta">
        <span class="chip hidden" id="a-board"></span>
        <span class="statusline"><span class="dot off" id="a-dot"></span><span id="a-status">—</span></span>
      </div>
      <div class="btnrow">
        <button class="btn" id="b-pause">暫停更新</button>
        <button class="btn hidden" id="b-reconnect">重新連線</button>
        <button class="btn hidden" id="b-open">在瀏覽器開啟</button>
        <button class="btn ghost" id="b-change">追蹤其他文章…</button>
      </div>
      <div id="changebox" class="hidden" style="margin-top:11px;display:flex;gap:8px">
        <input type="text" id="turl" placeholder="貼上文章網址，或輸入「看板 AID」">
        <button class="btn primary" id="b-go">追蹤</button>
      </div>
    </div></div>
    <div class="statrow">
      <div class="stat s-push"><div class="n" id="st-push">0</div><div class="l">推</div></div>
      <div class="stat s-arrow"><div class="n" id="st-arrow">0</div><div class="l">→</div></div>
      <div class="stat s-boo"><div class="n" id="st-boo">0</div><div class="l">噓</div></div>
      <div class="stat s-rate"><div class="n" id="st-rate">0</div><div class="l">則／分（近10分）</div></div>
    </div>
    <div class="card">
      <h3>推文熱度（近 15 分鐘）</h3>
      <div class="pad"><div class="spark" id="spark"></div></div>
    </div>
    <div class="card" id="topcard">
      <h3>活躍推文者</h3>
      <div id="toplist"><div class="empty">還沒有資料</div></div>
    </div>
    <div class="card feed">
      <h3>即時推文 <span class="note" id="feed-note"></span></h3>
      <div id="feed"></div>
    </div>
  </div>
  <!-- ============ 看板 ============ -->
  <div class="view" id="v-boards">
    <h1>看板</h1>
    <div id="boards-home">
      <div class="card">
        <h3>我的最愛 <span class="right" id="fav-refresh">重新整理</span></h3>
        <div id="favlist"><div class="empty">載入中…</div></div>
      </div>
    </div>
    <div id="boards-posts" class="hidden">
      <div class="backbar" id="b-back">
        <svg class="ic" style="width:14px;height:14px" viewBox="0 0 20 20"><path d="M12.5 4.5 7 10l5.5 5.5"/></svg>
        我的最愛</div>
      <div class="card hidden" id="pincard">
        <h3><svg class="ic" style="width:13px;height:13px" viewBox="0 0 20 20"><path d="M10 13.5V17M6.5 3.5h7l-1 5.5 2 2.5h-9l2-2.5z"/></svg>
          置頂</h3>
        <div id="pinlist"></div>
      </div>
      <div class="card">
        <h3 id="pl-title">最新文章</h3>
        <div id="postlist"><div class="empty">載入中…</div></div>
        <div class="loadmore hidden" id="b-more">載入更多</div>
      </div>
    </div>
  </div>
  <!-- ============ 歷史 ============ -->
  <div class="view" id="v-history">
    <h1>推文歷史</h1>
    <div class="searchbar">
      <input type="text" id="h-q" placeholder="搜尋作者或內容…">
      <div class="seg">
        <div class="seg-item active" data-f="all">全部</div>
        <div class="seg-item" data-f="推">推</div>
        <div class="seg-item" data-f="→">→</div>
        <div class="seg-item" data-f="噓">噓</div>
        <div class="seg-item" data-f="kw">關鍵字</div>
      </div>
    </div>
    <div class="card feed">
      <h3><span id="h-count">0 則</span><span class="right" id="h-refresh">重新整理</span></h3>
      <div id="hlist"><div class="empty">尚無資料</div></div>
    </div>
  </div>
  <!-- ============ 設定 ============ -->
  <div class="view" id="v-settings">
    <h1>設定</h1>
    <div class="card">
      <h3>狀態列顯示</h3>
      <div class="row"><div class="grow"><div class="t">視窗寬度</div></div>
        <input type="range" id="s-width" min="120" max="400" step="10">
        <div class="sliderval" id="s-width-v"></div></div>
      <div class="row"><div class="grow"><div class="t">捲動速度</div></div>
        <input type="range" id="s-speed" min="30" max="240" step="5">
        <div class="sliderval" id="s-speed-v"></div></div>
    </div>
    <div class="card">
      <h3>更新</h3>
      <div class="row"><div class="grow"><div class="t">推文更新頻率</div>
        <div class="s">每隔幾秒重新讀取文章的最新推文</div></div>
        <input type="range" id="s-poll" min="1" max="30" step="1">
        <div class="sliderval" id="s-poll-v"></div></div>
      <div class="row"><div class="grow"><div class="t">合併被切開的長留言</div>
        <div class="s">同帳號、同時間的接續推文自動接回同一則顯示</div></div>
        <div class="switch" id="s-merge"></div></div>
    </div>
    <div class="card">
      <h3>彈幕</h3>
      <div class="row"><div class="grow"><div class="t">開啟彈幕</div>
        <div class="s">新推文以彈幕飛過螢幕（透明置頂，不擋滑鼠點擊）。與聊天室互斥，一次只能開一種</div></div>
        <div class="switch" id="s-dmk"></div></div>
      <div class="row"><div class="grow"><div class="t">文字大小</div></div>
        <input type="range" id="s-dmk-font" min="12" max="36" step="1">
        <div class="sliderval" id="s-dmk-font-v"></div></div>
      <div class="row"><div class="grow"><div class="t">飛行速度</div></div>
        <input type="range" id="s-dmk-speed" min="60" max="400" step="10">
        <div class="sliderval" id="s-dmk-speed-v"></div></div>
      <div class="row"><div class="grow"><div class="t">不透明度</div></div>
        <input type="range" id="s-dmk-op" min="30" max="100" step="5">
        <div class="sliderval" id="s-dmk-op-v"></div></div>
      <div class="row"><div class="grow"><div class="t">半透明底色</div>
        <div class="s">文字加淺淺的深色圓角底，淺色/深色背景都清楚；關閉改用細描邊</div></div>
        <div class="switch" id="s-dmk-bg"></div></div>
      <div class="row"><div class="grow"><div class="t">底色透明度</div>
        <div class="s">越高底色越深、對比越強</div></div>
        <input type="range" id="s-dmk-bgop" min="10" max="80" step="5">
        <div class="sliderval" id="s-dmk-bgop-v"></div></div>
      <div class="row"><div class="grow"><div class="t">顯示範圍</div>
        <div class="s">彈幕佔用螢幕上方的比例（越大軌道越多、越滿版）</div></div>
        <input type="range" id="s-dmk-area" min="10" max="100" step="5">
        <div class="sliderval" id="s-dmk-area-v"></div></div>
      <div class="row"><div class="grow"><div class="t">依推噓型別上色</div>
        <div class="s">推綠、→黃、噓紅；關閉則一律白色</div></div>
        <div class="switch" id="s-dmk-color"></div></div>
      <div class="row"><div class="grow"><div class="t">顯示作者 id</div>
        <div class="s">彈幕顯示「作者: 內容」；關閉只顯示內容</div></div>
        <div class="switch" id="s-dmk-author"></div></div>
    </div>
    <div class="card">
      <h3>聊天室小視窗</h3>
      <div class="row"><div class="grow"><div class="t">開啟聊天室</div>
        <div class="s">實況聊天室風格的浮動視窗（可移動／拉伸／收合）。與彈幕互斥，一次只能開一種</div></div>
        <div class="switch" id="s-chat"></div></div>
      <div class="row"><div class="grow"><div class="t">文字大小</div></div>
        <input type="range" id="s-chat-font" min="11" max="24" step="1">
        <div class="sliderval" id="s-chat-font-v"></div></div>
      <div class="row"><div class="grow"><div class="t">背景透明度</div>
        <div class="s">越低越透明（看得到後面桌面）；文字保持清楚</div></div>
        <input type="range" id="s-chat-op" min="30" max="100" step="5">
        <div class="sliderval" id="s-chat-op-v"></div></div>
      <div class="row"><div class="grow"><div class="t">收合／展開</div>
        <div class="s">收合後變成一顆可拖曳的小 icon，點它再展開</div></div>
        <button class="btn" id="b-chat-collapse">收合</button></div>
    </div>
    <div class="card">
      <h3>膠囊主題</h3>
      <div id="themelist"></div>
    </div>
    <div class="card">
      <h3>關鍵字</h3>
      <div class="pad" style="padding-bottom:8px">
        <input type="text" id="s-kw" placeholder="輸入關鍵字，逗號或空白分隔（例：台GG, 大盤）">
        <div class="s" style="font-size:11.5px;color:var(--sub);margin-top:7px">
          含關鍵字的推文會在列表中高亮，狀態列以 ★ 標示</div>
      </div>
      <div class="row"><div class="grow"><div class="t">狀態列只顯示含關鍵字的推文</div>
        <div class="s">開啟後，跑馬燈只捲動符合關鍵字的最新推文</div></div>
        <div class="switch" id="s-kwfilter"></div></div>
    </div>
    <div class="card">
      <h3>帳號</h3>
      <div class="row"><div class="grow"><div class="t" id="s-acct">未登入</div>
        <div class="s">密碼儲存在 macOS 鑰匙圈</div></div>
        <button class="btn" id="b-relogin">原帳號重新登入</button>
        <button class="btn danger" id="b-logout">登出並清除帳密</button>
        <button class="btn primary hidden" id="b-login">登入</button></div>
    </div>
  </div>
</main>
<div id="toast"></div>
<script>
"use strict";
const $=id=>document.getElementById(id);
const send=(cmd,extra)=>{try{window.webkit.messageHandlers.py.postMessage(Object.assign({cmd},extra||{}))}catch(e){}};
let S=null;                       // 最新 state
let view="now";
let toastTimer=null;

function toast(msg){const t=$("toast");t.textContent=msg;t.classList.add("show");
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove("show"),2200);}

/* ---------- 導覽 ---------- */
document.querySelectorAll(".nav").forEach(n=>{
  n.addEventListener("click",()=>{
    document.querySelectorAll(".nav").forEach(x=>x.classList.remove("active"));
    n.classList.add("active");view=n.dataset.v;
    document.querySelectorAll(".view").forEach(v=>v.classList.remove("show"));
    $("v-"+view).classList.add("show");
    if(view==="boards"&&S&&(!S.favorites||!S.favorites.length))send("loadBoards");
    if(view==="history")send("getHistory");
  });
});

/* ---------- 追蹤 ---------- */
$("b-pause").addEventListener("click",()=>send(S&&S.article.paused?"resume":"pause"));
$("b-reconnect").addEventListener("click",()=>send("reconnect"));
$("b-open").addEventListener("click",()=>send("openBrowser"));
$("b-change").addEventListener("click",()=>{$("changebox").classList.toggle("hidden");$("turl").focus();});
$("b-go").addEventListener("click",()=>{const v=$("turl").value.trim();if(v){send("trackInput",{text:v});$("turl").value="";}});
$("turl").addEventListener("keydown",e=>{if(e.key==="Enter")$("b-go").click();});

function badge(t){const b=document.createElement("span");b.className="badge "+(t==="推"?"b-push":t==="噓"?"b-boo":"b-arrow");b.textContent=t;return b;}

function feedRow(c){
  const row=document.createElement("div");row.className="row"+(c.kw?" kw":"");
  row.appendChild(badge(c.t));
  const g=document.createElement("div");g.className="grow";
  const line=document.createElement("div");line.className="c-line";
  const au=document.createElement("span");au.className="au";au.textContent=c.a;
  line.appendChild(au);line.appendChild(document.createTextNode(c.c));
  g.appendChild(line);row.appendChild(g);
  const tm=document.createElement("div");tm.className="c-time";tm.textContent=c.tm;
  row.appendChild(tm);
  return row;
}

function renderFeed(){
  const box=$("feed");box.textContent="";
  const bf=(S.article&&S.article.backfill)||10;
  $("feed-note").textContent="僅此篇（起始載入最近 "+bf+" 則）";
  const cs=(S.comments||[]);
  if(!cs.length){const e=document.createElement("div");e.className="empty";
    e.textContent=S.article&&S.article.has?("尚無推文（開始追蹤後只載入最近 "+bf+" 則，之後即時更新）"):"還沒有推文";
    box.appendChild(e);return;}
  for(let i=cs.length-1;i>=0;i--)box.appendChild(feedRow(cs[i]));
}

function renderStats(){
  const st=S.stats||{push:0,boo:0,arrow:0,rate10:0,top:[],buckets:[]};
  $("st-push").textContent=st.push; $("st-boo").textContent=st.boo;
  $("st-arrow").textContent=st.arrow; $("st-rate").textContent=st.rate10;
  const sp=$("spark");sp.textContent="";
  const bs=st.buckets||[];const mx=Math.max(1,...bs);
  bs.forEach(v=>{const b=document.createElement("div");b.className="bar";
    b.style.height=Math.max(4,Math.round(v/mx*100))+"%";
    b.style.opacity=v?"1":".22";sp.appendChild(b);});
  const tl=$("toplist");tl.textContent="";
  const top=st.top||[];
  if(!top.length){const e=document.createElement("div");e.className="empty";e.textContent="還沒有資料";tl.appendChild(e);return;}
  const mxc=top[0][1]||1;
  top.forEach(([au,n])=>{
    const row=document.createElement("div");row.className="row";
    const g=document.createElement("div");g.className="grow";
    const t=document.createElement("div");t.className="t";t.textContent=au;
    const bar=document.createElement("div");bar.className="toprow-bar";
    const fill=document.createElement("div");fill.style.width=Math.round(n/mxc*100)+"%";
    bar.appendChild(fill);g.appendChild(t);g.appendChild(bar);row.appendChild(g);
    const c=document.createElement("div");c.className="c-time";c.textContent=n+" 則";row.appendChild(c);
    tl.appendChild(row);
  });
}

/* ---------- 看板 ---------- */
$("fav-refresh").addEventListener("click",()=>send("loadBoards"));
$("b-back").addEventListener("click",()=>{$("boards-posts").classList.add("hidden");$("boards-home").classList.remove("hidden");});

function renderBoards(){
  const list=$("favlist");list.textContent="";
  const favs=S.favorites||[];
  if(!favs.length){const e=document.createElement("div");e.className="empty";
    e.textContent=S.loggedIn?"載入中…（或最愛清單是空的）":"請先登入";list.appendChild(e);return;}
  favs.forEach(f=>{
    const row=document.createElement("div");row.className="row click";
    const g=document.createElement("div");g.className="grow";
    const t=document.createElement("div");t.className="t";t.textContent=f.b;
    const s=document.createElement("div");s.className="s";s.textContent=f.t||"";
    g.appendChild(t);g.appendChild(s);row.appendChild(g);
    const ch=document.createElement("div");ch.className="chev";ch.textContent="›";row.appendChild(ch);
    row.addEventListener("click",()=>{send("loadPosts",{board:f.b});
      $("boards-home").classList.add("hidden");$("boards-posts").classList.remove("hidden");
      $("pl-title").textContent=f.b+"　最新文章";
      $("pincard").classList.add("hidden");$("b-more").classList.add("hidden");
      $("postlist").innerHTML='<div class="empty">載入中…</div>';});
    list.appendChild(row);
  });
}

function nrecClass(v){
  if(!v)return "b-n0";
  if(v==="爆")return "b-hot";
  if(v[0]==="X"||v[0]==="x")return "b-x";       // 噓到負的（X1~XX）
  const n=parseInt(v,10);
  if(isNaN(n))return "b-num";
  return n>=10?"b-n2":"b-n1";                    // 10-99 黃、1-9 綠
}
function postRow(br,p){
  const row=document.createElement("div");row.className="row click";
  const nb=document.createElement("span");nb.className="badge "+nrecClass(p.p);
  nb.textContent=p.p||"·";row.appendChild(nb);
  const g=document.createElement("div");g.className="grow";
  const t=document.createElement("div");t.className="t";t.textContent=p.t||"(無標題)";
  const s=document.createElement("div");s.className="s";s.textContent=(p.a||"")+(p.d?"　"+p.d:"");
  g.appendChild(t);g.appendChild(s);row.appendChild(g);
  row.addEventListener("click",()=>{
    send("track",{board:br.board,url:p.url||"",index:p.i||0,title:p.t||""});
    toast("開始追蹤：「"+(p.t||br.board)+"」");
    document.querySelector('.nav[data-v="now"]').click();});
  return row;
}
function renderPosts(){
  const br=S.browse||{};
  if(!br.board)return;
  $("pl-title").textContent=br.board+"　最新文章"+((br.loading&&(br.posts||[]).length)?"（更新中…）":"");
  // 置頂區塊
  const pins=br.pinned||[];
  $("pincard").classList.toggle("hidden",!pins.length);
  const pl=$("pinlist");pl.textContent="";
  pins.forEach(p=>pl.appendChild(postRow(br,p)));
  // 文章列表（Python 已排成新→舊，含載入更多接在後面的較舊文章）
  const list=$("postlist");
  const ps=br.posts||[];
  if(br.loading&&!ps.length){list.innerHTML='<div class="empty">載入中…</div>';}
  else{
    list.textContent="";
    if(!ps.length){const e=document.createElement("div");e.className="empty";e.textContent="讀不到文章列表";list.appendChild(e);}
    else ps.forEach(p=>list.appendChild(postRow(br,p)));
  }
  // 載入更多（下滑到底自動載入；按鈕降級為狀態指示，點擊仍可手動觸發）
  const more=$("b-more");
  more.classList.toggle("hidden",!br.prev);
  more.classList.toggle("loading",!!br.loading);
  more.textContent=br.loading?"載入中…":"下滑自動載入更多";
  // 內容太短填不滿視窗（捲不動）→ 直接補載下一頁
  if(!br.loading&&br.prev&&postsPanelVisible()&&
     mainEl.scrollHeight<=mainEl.clientHeight+40)requestMore();
}
function postsPanelVisible(){
  return view==="boards"&&!$("boards-posts").classList.contains("hidden");
}
let moreReq=false;   // 已送出 loadMore、還沒收到新狀態（防捲動事件重複觸發）
function requestMore(){
  if(moreReq||!S||!S.browse||!S.browse.prev||S.browse.loading)return;
  moreReq=true;send("loadMore");
}
$("b-more").addEventListener("click",requestMore);
const mainEl=document.querySelector("main");
mainEl.addEventListener("scroll",()=>{
  if(!postsPanelVisible())return;
  if(mainEl.scrollTop+mainEl.clientHeight>=mainEl.scrollHeight-320)requestMore();
},{passive:true});

/* ---------- 歷史 ---------- */
let H=[];            // 完整歷史（getHistory 推來）
let hFilter="all";
window.__history=function(rows){H=rows||[];renderHistory();};
$("h-q").addEventListener("input",()=>renderHistory());
$("h-refresh").addEventListener("click",()=>send("getHistory"));
document.querySelectorAll(".seg-item").forEach(ch=>{
  ch.addEventListener("click",()=>{
    document.querySelectorAll(".seg-item").forEach(x=>x.classList.remove("active"));
    ch.classList.add("active");hFilter=ch.dataset.f;renderHistory();
  });
});
function renderHistory(){
  const q=$("h-q").value.trim().toLowerCase();
  let rows=H;
  if(hFilter==="kw")rows=rows.filter(c=>c.kw);
  else if(hFilter!=="all")rows=rows.filter(c=>c.t===hFilter);
  if(q)rows=rows.filter(c=>(c.a+" "+c.c).toLowerCase().indexOf(q)>=0);
  $("h-count").textContent=rows.length+" 則";
  const box=$("hlist");box.textContent="";
  if(!rows.length){const e=document.createElement("div");e.className="empty";e.textContent="沒有符合的推文";box.appendChild(e);return;}
  const cap=Math.max(0,rows.length-500);   // 顯示上限 500（最新優先）
  for(let i=rows.length-1;i>=cap;i--)box.appendChild(feedRow(rows[i]));
}

/* ---------- 設定 ---------- */
function bindSlider(id,key,unit){
  const el=$(id),val=$(id+"-v");
  el.addEventListener("input",()=>{val.textContent=el.value+unit;});
  el.addEventListener("change",()=>send("set",{key,value:parseFloat(el.value)}));
}
function bindSwitch(id,key){
  $(id).addEventListener("click",()=>{
    const on=!$(id).classList.contains("on");
    $(id).classList.toggle("on",on);
    send("set",{key,value:on});
  });
}
bindSlider("s-width","window_px"," px");
bindSlider("s-speed","speed_px"," px/s");
bindSlider("s-poll","poll_sec"," 秒");
bindSlider("s-dmk-font","danmaku_font"," px");
bindSlider("s-dmk-speed","danmaku_speed"," px/s");
bindSlider("s-dmk-op","danmaku_opacity"," %");
bindSlider("s-dmk-bgop","danmaku_bg_alpha"," %");
bindSlider("s-dmk-area","danmaku_area"," %");
bindSwitch("s-dmk","danmaku_on");
bindSwitch("s-dmk-bg","danmaku_backdrop");
bindSwitch("s-dmk-color","danmaku_type_color");
bindSwitch("s-dmk-author","danmaku_author");
bindSlider("s-chat-font","chat_font"," px");
bindSlider("s-chat-op","chat_opacity"," %");
bindSwitch("s-chat","chat_on");
$("b-chat-collapse").addEventListener("click",()=>send(S&&S.settings&&S.settings.chat_collapsed?"chatExpand":"chatCollapse"));
$("b-relogin").addEventListener("click",()=>send("relogin"));
$("b-logout").addEventListener("click",()=>send("logout"));
$("b-login").addEventListener("click",()=>send("login"));
$("s-kw").addEventListener("change",()=>send("set",{key:"keywords",value:$("s-kw").value}));
$("s-kw").addEventListener("keydown",e=>{if(e.key==="Enter")e.target.blur();});
$("s-kwfilter").addEventListener("click",()=>{
  const on=!$("s-kwfilter").classList.contains("on");
  $("s-kwfilter").classList.toggle("on",on);
  send("set",{key:"keyword_filter",value:on});
});
$("s-merge").addEventListener("click",()=>{
  const on=!$("s-merge").classList.contains("on");
  $("s-merge").classList.toggle("on",on);
  send("set",{key:"merge_comments",value:on});
});

function renderThemes(){
  const box=$("themelist");box.textContent="";
  (S.themes||[]).forEach(th=>{
    const row=document.createElement("div");row.className="row click";
    const chk=document.createElement("div");chk.className="check";
    chk.textContent=(S.settings.pill_theme===th.name)?"✓":"";row.appendChild(chk);
    const g=document.createElement("div");g.className="grow";
    const t=document.createElement("div");t.className="t";t.textContent=th.label;
    g.appendChild(t);row.appendChild(g);
    const dots=document.createElement("div");dots.className="dots";
    (th.dots||[]).forEach(c=>{const d=document.createElement("div");d.className="cdot";
      d.style.background=c;dots.appendChild(d);});
    row.appendChild(dots);
    row.addEventListener("click",()=>send("set",{key:"pill_theme",value:th.name}));
    box.appendChild(row);
  });
}

/* ---------- 全域渲染 ---------- */
window.__update=function(state){
  S=state;
  moreReq=false;   // 新狀態到了 → 解鎖自動載入（捲動可再次觸發）
  $("acct").textContent=S.loggedIn?("@"+(S.pttId||"")):"未登入";
  $("s-acct").textContent=S.loggedIn?("@"+(S.pttId||"")):"未登入";
  $("b-relogin").classList.toggle("hidden",!S.loggedIn);
  $("b-logout").classList.toggle("hidden",!S.loggedIn);
  $("b-login").classList.toggle("hidden",!!S.loggedIn);
  $("connline").textContent=S.article.status||"";
  // 追蹤卡
  const a=S.article;
  $("a-title").textContent=a.title||(a.has?"讀取中…":"尚未追蹤文章");
  $("a-board").textContent=a.board||"";
  $("a-board").classList.toggle("hidden",!a.board);
  $("a-status").textContent=a.status||"—";
  const dot=$("a-dot");
  const warn=a.status&&/重連|失敗|停止|異常|中斷|不存在|已刪除/.test(a.status);
  dot.className="dot"+(warn?" warn":(a.has?"":" off"));
  $("b-pause").textContent=a.paused?"繼續更新":"暫停更新";
  $("b-reconnect").classList.toggle("hidden",!warn);   // 連線異常時才顯示「重新連線」
  $("b-open").classList.toggle("hidden",!a.url);
  renderFeed();
  renderStats();
  renderBoards();
  renderPosts();
  renderThemes();
  // 設定（未 focus 才覆寫，避免拉滑桿時跳動）
  if(document.activeElement!==$("s-width")){$("s-width").value=S.settings.window_px;$("s-width-v").textContent=S.settings.window_px+" px";}
  if(document.activeElement!==$("s-speed")){$("s-speed").value=S.settings.speed_px;$("s-speed-v").textContent=S.settings.speed_px+" px/s";}
  if(document.activeElement!==$("s-poll")){$("s-poll").value=S.settings.poll_sec;$("s-poll-v").textContent=S.settings.poll_sec+" 秒";}
  if(document.activeElement!==$("s-kw")){$("s-kw").value=S.settings.keywords||"";}
  if(document.activeElement!==$("s-dmk-font")){$("s-dmk-font").value=S.settings.danmaku_font;$("s-dmk-font-v").textContent=S.settings.danmaku_font+" px";}
  if(document.activeElement!==$("s-dmk-speed")){$("s-dmk-speed").value=S.settings.danmaku_speed;$("s-dmk-speed-v").textContent=S.settings.danmaku_speed+" px/s";}
  if(document.activeElement!==$("s-dmk-op")){$("s-dmk-op").value=S.settings.danmaku_opacity;$("s-dmk-op-v").textContent=S.settings.danmaku_opacity+" %";}
  if(document.activeElement!==$("s-dmk-bgop")){$("s-dmk-bgop").value=S.settings.danmaku_bg_alpha;$("s-dmk-bgop-v").textContent=S.settings.danmaku_bg_alpha+" %";}
  if(document.activeElement!==$("s-dmk-area")){$("s-dmk-area").value=S.settings.danmaku_area;$("s-dmk-area-v").textContent=S.settings.danmaku_area+" %";}
  $("s-dmk").classList.toggle("on",!!S.settings.danmaku_on);
  $("s-dmk-bg").classList.toggle("on",!!S.settings.danmaku_backdrop);
  $("s-dmk-color").classList.toggle("on",!!S.settings.danmaku_type_color);
  $("s-dmk-author").classList.toggle("on",!!S.settings.danmaku_author);
  if(document.activeElement!==$("s-chat-font")){$("s-chat-font").value=S.settings.chat_font;$("s-chat-font-v").textContent=S.settings.chat_font+" px";}
  if(document.activeElement!==$("s-chat-op")){$("s-chat-op").value=S.settings.chat_opacity;$("s-chat-op-v").textContent=S.settings.chat_opacity+" %";}
  $("s-chat").classList.toggle("on",!!S.settings.chat_on);
  $("b-chat-collapse").textContent=S.settings.chat_collapsed?"展開":"收合";
  $("b-chat-collapse").disabled=!S.settings.chat_on;
  $("s-kwfilter").classList.toggle("on",!!S.settings.keyword_filter);
  $("s-merge").classList.toggle("on",!!S.settings.merge_comments);
  if(S.toast){toast(S.toast);}
};
send("ready");
</script>
</body>
</html>
"""
