(() => {
  const P = window.P;
  P.zeroReferenceMarkup = () => `<section class="card zero-reference-card">
    <div class="card-head"><h2>零位参考</h2>${P.badge('仅参考')}</div>
    <div class="zero-reference-toolbar" aria-label="零位参考视角">
      <div class="segmented">${[['front','正面'],['side','侧面'],['back','背面']].map(([view,label])=>`<button type="button" data-zero-view="${view}" aria-pressed="${view==='front'}">${label}</button>`).join('')}</div>
      <button type="button" class="btn small" data-zero-view="reset" aria-label="回正零位参考视角">${P.icon('refresh',13)}</button>
      <button type="button" class="btn small" data-zero-labels aria-pressed="false">编号</button>
    </div>
    <div class="zero-reference-stage" data-zero-stage role="img" aria-label="小鸭零位三维模型">
      <div class="zero-reference-loading" data-zero-status>正在加载模型…</div>
    </div>
    <div class="zero-reference-caption"><strong>腿伸直、脚板垂直、头摆正、嘴闭合。</strong><span>对照摆好后，再点对应舵机的“中位”。</span></div>
  </section>`;
})();
