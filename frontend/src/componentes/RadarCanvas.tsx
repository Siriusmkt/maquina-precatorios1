/**
 * Radar em canvas: varredura conica com rastro, aneis, setores por tribunal
 * e blips que "acendem" quando a varredura passa. 60 fps, pausa quando a aba
 * ou o componente saem de vista, e fica estatico com movimento reduzido.
 */
import { useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

export interface Blip {
  id: string;
  angulo: number; // radianos, 0 = direita, sentido horario
  raio: number; // 0 (centro) .. 1 (borda)
  cor: string;
  rotulo?: string;
  tamanho?: number;
}

export interface Setor {
  rotulo: string;
  de: number;
  ate: number;
}

interface Props {
  blips: Blip[];
  setores?: Setor[];
  velocidade?: number; // rad/s
  className?: string;
  mostrarRotulos?: boolean;
  aoPassar?: (blip: Blip | null, x: number, y: number) => void;
  aoClicar?: (blip: Blip) => void;
}

const DOIS_PI = Math.PI * 2;
const RASTRO = Math.PI / 2.4;

function normalizar(a: number): number {
  return ((a % DOIS_PI) + DOIS_PI) % DOIS_PI;
}

export function RadarCanvas({
  blips,
  setores = [],
  velocidade = 0.55,
  className,
  mostrarRotulos = true,
  aoPassar,
  aoClicar,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduzido = useReducedMotion();
  const estado = useRef({ blips, setores, aoPassar, aoClicar, mouse: null as { x: number; y: number } | null });
  estado.current.blips = blips;
  estado.current.setores = setores;
  estado.current.aoPassar = aoPassar;
  estado.current.aoClicar = aoClicar;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let largura = 0;
    let altura = 0;
    let quadro = 0;
    let visivel = true;
    const inicio = performance.now();
    const brilho = new Map<string, number>(); // ultimo instante em que a varredura passou

    const redimensionar = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const r = canvas.getBoundingClientRect();
      largura = r.width;
      altura = r.height;
      canvas.width = Math.round(largura * dpr);
      canvas.height = Math.round(altura * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    redimensionar();
    const ro = new ResizeObserver(redimensionar);
    ro.observe(canvas);
    const io = new IntersectionObserver(([e]) => {
      visivel = Boolean(e?.isIntersecting);
    });
    io.observe(canvas);

    const posicao = (b: Blip, cx: number, cy: number, R: number) => ({
      x: cx + Math.cos(b.angulo) * b.raio * R,
      y: cy + Math.sin(b.angulo) * b.raio * R,
    });

    const desenhar = (agora: number) => {
      quadro = requestAnimationFrame(desenhar);
      if (!visivel || document.hidden) return;
      const t = (agora - inicio) / 1000;
      const cx = largura / 2;
      const cy = altura / 2;
      const R = Math.min(largura, altura) / 2 - 18;
      const varredura = reduzido ? -Math.PI / 4 : normalizar(t * velocidade);

      ctx.clearRect(0, 0, largura, altura);

      // Aneis e cruz
      ctx.lineWidth = 1;
      for (let i = 1; i <= 4; i++) {
        ctx.beginPath();
        ctx.arc(cx, cy, (R * i) / 4, 0, DOIS_PI);
        ctx.strokeStyle = `rgba(78, 240, 196, ${0.05 + i * 0.022})`;
        ctx.stroke();
      }
      ctx.strokeStyle = "rgba(255,255,255,0.05)";
      ctx.beginPath();
      ctx.moveTo(cx - R, cy);
      ctx.lineTo(cx + R, cy);
      ctx.moveTo(cx, cy - R);
      ctx.lineTo(cx, cy + R);
      ctx.stroke();

      // Marcacoes na borda
      for (let g = 0; g < 360; g += 5) {
        const a = (g * Math.PI) / 180;
        const tam = g % 30 === 0 ? 9 : 4;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * (R + 4), cy + Math.sin(a) * (R + 4));
        ctx.lineTo(cx + Math.cos(a) * (R + 4 + tam), cy + Math.sin(a) * (R + 4 + tam));
        ctx.strokeStyle = g % 30 === 0 ? "rgba(78,240,196,0.35)" : "rgba(255,255,255,0.08)";
        ctx.stroke();
      }

      // Setores (tribunais)
      ctx.font = "500 10px 'Geist Mono Variable', ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const s of estado.current.setores) {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(s.de) * R, cy + Math.sin(s.de) * R);
        ctx.strokeStyle = "rgba(255,255,255,0.06)";
        ctx.stroke();
        const meio = (s.de + s.ate) / 2;
        ctx.fillStyle = "rgba(182,191,209,0.55)";
        ctx.fillText(s.rotulo, cx + Math.cos(meio) * (R - 16), cy + Math.sin(meio) * (R - 16));
      }

      // Rastro da varredura (gradiente conico)
      if ("createConicGradient" in ctx) {
        const g = ctx.createConicGradient(varredura - RASTRO, cx, cy);
        g.addColorStop(0, "rgba(78,240,196,0)");
        g.addColorStop(RASTRO / DOIS_PI, "rgba(78,240,196,0.32)");
        g.addColorStop(RASTRO / DOIS_PI + 0.0001, "rgba(78,240,196,0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, R, varredura - RASTRO, varredura);
        ctx.closePath();
        ctx.fill();
      }
      // Linha da frente
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(varredura) * R, cy + Math.sin(varredura) * R);
      ctx.strokeStyle = "rgba(163,249,225,0.9)";
      ctx.lineWidth = 1.5;
      ctx.shadowColor = "rgba(78,240,196,0.9)";
      ctx.shadowBlur = 12;
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.lineWidth = 1;

      // Blips
      for (const b of estado.current.blips) {
        const atras = normalizar(varredura - b.angulo);
        if (atras < 0.05 && !reduzido) brilho.set(b.id, t);
        const desde = brilho.has(b.id) ? t - (brilho.get(b.id) ?? 0) : 99;
        const intensidade = reduzido ? 0.7 : Math.max(0.22, 1 - desde / 3.2);
        const { x, y } = posicao(b, cx, cy, R);
        const tam = (b.tamanho ?? 3) * (0.8 + intensidade * 0.6);

        if (desde < 1.2 && !reduzido) {
          ctx.beginPath();
          ctx.arc(x, y, tam + desde * 26, 0, DOIS_PI);
          ctx.strokeStyle = b.cor.replace("ALFA", String(0.5 * (1 - desde / 1.2)));
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(x, y, tam, 0, DOIS_PI);
        ctx.fillStyle = b.cor.replace("ALFA", String(intensidade));
        ctx.shadowColor = b.cor.replace("ALFA", "1");
        ctx.shadowBlur = 14 * intensidade;
        ctx.fill();
        ctx.shadowBlur = 0;

        if (mostrarRotulos && b.rotulo && intensidade > 0.55) {
          ctx.font = "500 10.5px 'Geist Mono Variable', ui-monospace, monospace";
          ctx.textAlign = "left";
          ctx.fillStyle = `rgba(238,241,248,${(intensidade - 0.4) * 1.2})`;
          ctx.fillText(b.rotulo, x + 9, y - 9);
        }
      }

      // Centro
      const pulso = reduzido ? 1 : 0.6 + Math.sin(t * 2.2) * 0.4;
      ctx.beginPath();
      ctx.arc(cx, cy, 4.5, 0, DOIS_PI);
      ctx.fillStyle = "#f5c66b";
      ctx.shadowColor = "rgba(245,198,107,1)";
      ctx.shadowBlur = 18 * pulso;
      ctx.fill();
      ctx.shadowBlur = 0;
    };
    quadro = requestAnimationFrame(desenhar);

    const maisProximo = (mx: number, my: number): Blip | null => {
      const cx = largura / 2;
      const cy = altura / 2;
      const R = Math.min(largura, altura) / 2 - 18;
      let melhor: Blip | null = null;
      let dist = 14;
      for (const b of estado.current.blips) {
        const p = posicao(b, cx, cy, R);
        const d = Math.hypot(p.x - mx, p.y - my);
        if (d < dist) {
          dist = d;
          melhor = b;
        }
      }
      return melhor;
    };
    const mover = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      const blip = maisProximo(e.clientX - r.left, e.clientY - r.top);
      canvas.style.cursor = blip ? "pointer" : "default";
      estado.current.aoPassar?.(blip, e.clientX - r.left, e.clientY - r.top);
    };
    const clicar = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      const blip = maisProximo(e.clientX - r.left, e.clientY - r.top);
      if (blip) estado.current.aoClicar?.(blip);
    };
    const sair = () => estado.current.aoPassar?.(null, 0, 0);
    canvas.addEventListener("pointermove", mover);
    canvas.addEventListener("pointerleave", sair);
    canvas.addEventListener("click", clicar as EventListener);

    return () => {
      cancelAnimationFrame(quadro);
      ro.disconnect();
      io.disconnect();
      canvas.removeEventListener("pointermove", mover);
      canvas.removeEventListener("pointerleave", sair);
      canvas.removeEventListener("click", clicar as EventListener);
    };
  }, [reduzido, velocidade, mostrarRotulos]);

  return <canvas ref={canvasRef} className={cn("block size-full", className)} role="img" aria-label="Radar de precatórios" />;
}
