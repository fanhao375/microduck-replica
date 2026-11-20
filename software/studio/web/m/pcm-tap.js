// 采 PCM 的小工人：AudioContext 每跑一帧（128 样本）就把这一帧的浮点样本丢回主线程。
// 单独成一个文件而不是塞在页面里的 blob:，是因为 iOS Safari 用 blob: 加载 AudioWorklet
// 经常直接失败（拿到就是「长按没反应」）。
class PcmTap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice());
    return true; // 保持活着，process 会被一直调用
  }
}
registerProcessor('pcm-tap', PcmTap);
