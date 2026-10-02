#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
insta-dl.pyw - janela do insta-dl.

Extensao .pyw de proposito: o Windows abre com o pythonw.exe, que NAO mostra
console. O usuario ve uma janela comum, nao uma tela preta de script.

Toda a logica vive no baixar.py; aqui so tem interface. Sem dependencia
externa: tkinter faz parte da biblioteca padrao do Python.
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baixar  # noqa: E402

try:
    import atualizacao
except ImportError:
    atualizacao = None

AQUI = os.path.dirname(os.path.abspath(__file__))
# No pacote portatil os fontes ficam em app\ e o insta-dl.exe na pasta de cima.
# Nesse caso os videos vao para a raiz, que e onde a pessoa realmente olha.
_ACIMA = os.path.dirname(AQUI)
RAIZ = _ACIMA if os.path.exists(os.path.join(_ACIMA, "insta-dl.exe")) else AQUI
PASTA_VIDEOS = os.path.join(RAIZ, "videos")


class Janela(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("insta-dl — baixar video do Instagram")
        self.geometry("620x420")
        self.minsize(520, 360)
        self.fila = queue.Queue()
        self.ocupado = False
        self.ultimo_arquivo = None

        self._montar()
        self._colar_da_area_de_transferencia()
        self.after(100, self._drenar_fila)
        if atualizacao:
            threading.Thread(target=self._checar_atualizacao, daemon=True).start()

    # ------------------------------------------------------------ interface

    def _montar(self):
        corpo = ttk.Frame(self, padding=16)
        corpo.pack(fill="both", expand=True)
        corpo.columnconfigure(0, weight=1)

        ttk.Label(corpo, text="Link do post ou reel",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(corpo, foreground="#666",
                  text="No Instagram: menu ··· → Copiar link. "
                       "Aqui o campo já vem preenchido."
                  ).grid(row=1, column=0, sticky="w", pady=(0, 6))

        linha = ttk.Frame(corpo)
        linha.grid(row=2, column=0, sticky="ew")
        linha.columnconfigure(0, weight=1)
        self.campo = ttk.Entry(linha, font=("Consolas", 10))
        self.campo.grid(row=0, column=0, sticky="ew", ipady=4)
        self.campo.bind("<Return>", lambda e: self.baixar())
        self.botao = ttk.Button(linha, text="Baixar", width=12, command=self.baixar)
        self.botao.grid(row=0, column=1, padx=(8, 0))

        self.barra = ttk.Progressbar(corpo, mode="determinate", maximum=100)
        self.barra.grid(row=3, column=0, sticky="ew", pady=(14, 4))

        self.status = ttk.Label(corpo, text="Pronto.", foreground="#444")
        self.status.grid(row=4, column=0, sticky="w")

        quadro = ttk.LabelFrame(corpo, text="Andamento", padding=8)
        quadro.grid(row=5, column=0, sticky="nsew", pady=(12, 10))
        corpo.rowconfigure(5, weight=1)
        quadro.columnconfigure(0, weight=1)
        quadro.rowconfigure(0, weight=1)
        self.texto = tk.Text(quadro, height=8, wrap="word", font=("Consolas", 9),
                             background="#f7f7f7", relief="flat", state="disabled")
        self.texto.grid(row=0, column=0, sticky="nsew")
        rolagem = ttk.Scrollbar(quadro, command=self.texto.yview)
        rolagem.grid(row=0, column=1, sticky="ns")
        self.texto.configure(yscrollcommand=rolagem.set)

        rodape = ttk.Frame(corpo)
        rodape.grid(row=6, column=0, sticky="ew")
        rodape.columnconfigure(0, weight=1)
        ttk.Label(rodape, text=f"versao {self._versao()}",
                  foreground="#888").grid(row=0, column=0, sticky="w")
        ttk.Button(rodape, text="Abrir pasta dos videos",
                   command=self.abrir_pasta).grid(row=0, column=1)

    def _versao(self):
        return atualizacao.ler_versao() if atualizacao else "-"

    def _colar_da_area_de_transferencia(self):
        try:
            texto = self.clipboard_get()
        except tk.TclError:
            return
        if baixar.achar_shortcode(texto or ""):
            self.campo.insert(0, texto.strip().split()[0])
            self.escrever("Link encontrado na area de transferencia.")

    # ------------------------------------------------- comunicacao com a thread

    def escrever(self, msg):
        self.fila.put(("log", str(msg)))

    def _drenar_fila(self):
        try:
            while True:
                tipo, valor = self.fila.get_nowait()
                if tipo == "log":
                    self.texto.configure(state="normal")
                    self.texto.insert("end", valor.rstrip() + "\n")
                    self.texto.see("end")
                    self.texto.configure(state="disabled")
                elif tipo == "progresso":
                    feito, total = valor
                    if total:
                        self.barra.configure(mode="determinate")
                        self.barra["value"] = feito * 100 / total
                        self.status.configure(
                            text=f"Baixando... {feito * 100 // total}% "
                                 f"({feito / 1048576:.1f} MB)")
                    else:
                        self.status.configure(
                            text=f"Baixando... {feito / 1048576:.1f} MB")
                elif tipo == "status":
                    self.status.configure(text=valor)
                elif tipo == "fim":
                    self._terminou(valor)
        except queue.Empty:
            pass
        self.after(100, self._drenar_fila)

    def _terminou(self, deu_certo):
        self.ocupado = False
        self.botao.configure(state="normal", text="Baixar")
        self.barra["value"] = 100 if deu_certo else 0
        self.status.configure(
            text="Pronto. Video na pasta videos\\." if deu_certo
            else "Nao consegui baixar esse post. Veja o andamento acima.",
            foreground="#0a0" if deu_certo else "#c00")

    # ------------------------------------------------------------- acoes

    def baixar(self):
        if self.ocupado:
            return
        url = self.campo.get().strip()
        if not baixar.achar_shortcode(url):
            self.status.configure(
                text="Cole um link de post ou reel do Instagram.", foreground="#c00")
            return
        self.ocupado = True
        self.botao.configure(state="disabled", text="Baixando")
        self.barra["value"] = 0
        self.status.configure(text="Lendo a pagina do post...", foreground="#444")
        threading.Thread(target=self._trabalhar, args=(url,), daemon=True).start()

    def _trabalhar(self, url):
        baixar.log = self.escrever
        baixar.PROGRESSO = lambda feito, total: self.fila.put(
            ("progresso", (feito, total)))
        deu_certo = False
        try:
            os.makedirs(PASTA_VIDEOS, exist_ok=True)
            antes = set(os.listdir(PASTA_VIDEOS))
            deu_certo = baixar.processar(
                baixar.Sessao(), url, PASTA_VIDEOS,
                salvar_legenda=True, usar_plano_b=True, cookies=None,
                so_plano_b=False)
            novos = [a for a in os.listdir(PASTA_VIDEOS)
                     if a not in antes and a.endswith(".mp4")]
            if novos:
                self.ultimo_arquivo = os.path.join(PASTA_VIDEOS, novos[0])
        except Exception as e:                       # a janela nao pode morrer
            self.escrever(f"! erro inesperado: {type(e).__name__}: {e}")
        finally:
            baixar.PROGRESSO = None
            self.fila.put(("fim", deu_certo))

    def abrir_pasta(self):
        os.makedirs(PASTA_VIDEOS, exist_ok=True)
        try:
            os.startfile(PASTA_VIDEOS)               # noqa: S606 - Windows
        except OSError as e:
            self.escrever(f"! nao consegui abrir a pasta: {e}")

    def _checar_atualizacao(self):
        try:
            nova = atualizacao.verificar()
        except Exception:
            return
        if nova:
            self.escrever(f"Ferramenta atualizada para {nova}. "
                          "A versao nova vale na proxima vez que abrir.")


def autoteste(url):
    """Exercita a fiacao da janela sem ninguem clicando (usado nos testes)."""
    app = Janela()
    app.campo.delete(0, "end")
    app.campo.insert(0, url)
    resultado = {}

    fim_original = app._terminou

    def fim(deu_certo):
        fim_original(deu_certo)
        resultado["ok"] = deu_certo
        resultado["barra"] = app.barra["value"]
        resultado["status"] = app.status.cget("text")
        resultado["arquivo"] = app.ultimo_arquivo
        app.texto.configure(state="normal")
        resultado["log"] = app.texto.get("1.0", "end").strip()
        app.after(200, app.destroy)

    app._terminou = fim
    app.after(300, app.baixar)
    app.after(180000, app.destroy)                   # trava de seguranca
    app.mainloop()
    return resultado


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--autoteste":
        # Rodando pelo insta-dl.exe (pythonw) nao existe stdout, e num console
        # cp1252 um emoji da legenda derruba o print. Nenhum dos dois pode
        # fazer o teste "falhar" por um motivo que nao e o do teste.
        def diga(texto):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass
            try:
                print(texto)
            except (OSError, ValueError, UnicodeError, AttributeError):
                pass

        r = autoteste(sys.argv[2])
        for rotulo in ("ok", "barra", "status", "arquivo"):
            diga("%s: %s" % (rotulo, r.get(rotulo)))
        diga("--- log da janela ---")
        diga(r.get("log"))
        sys.exit(0 if r.get("ok") else 1)
    Janela().mainloop()
