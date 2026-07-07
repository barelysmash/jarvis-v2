// Mic capture for the local audio level reading. Only used if the backend
// isn't already publishing audio levels over WebSocket.

export async function getMicAudioLevel(
  onLevel: (level: number) => void,
  onError?: (err: Error) => void
) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);

    const data = new Uint8Array(analyser.frequencyBinCount);

    let raf: number;
    const tick = () => {
      analyser.getByteFrequencyData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i];
      const avg = sum / data.length / 255;
      onLevel(avg);
      raf = requestAnimationFrame(tick);
    };
    tick();

    return () => {
      cancelAnimationFrame(raf);
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
    };
  } catch (err) {
    onError?.(err as Error);
    return () => {};
  }
}
