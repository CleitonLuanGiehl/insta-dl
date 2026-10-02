#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
baixar.py - baixa video de post/reel do Instagram, 100% local.

Plano A (padrao): extrator proprio, usando SO a biblioteca padrao do Python.
Nao passa por site intermediario: fala direto com o instagram.com e baixa o
mp4 do CDN (fbcdn), do mesmo jeito que o navegador faria.

Plano B (automatico): se o plano A falhar - Instagram mudou o HTML, ou o post
exige login - cai sozinho no yt-dlp. Se o yt-dlp nao estiver instalado, ele
mesmo baixa (executavel unico do GitHub oficial, ~18 MB; se nao der, instala
o pacote pip). Nao pergunta nada, so avisa o que esta fazendo.

Uso:
    python baixar.py https://www.instagram.com/p/XXXXXXXXX/
    python baixar.py                         (usa o link da area de transferencia)
    python baixar.py URL1 URL2 ...           (varios de uma vez)

Opcoes:
    -o PASTA         pasta de destino (padrao: ./videos)
    --legenda        salva tambem a legenda do post num .txt ao lado do video
    --plano-b        vai direto no yt-dlp, sem tentar o extrator proprio
    --sem-plano-b    so o extrator proprio; nao instala nem usa o yt-dlp
    --cookies NAV    (plano B) usa o login do navegador: chrome, edge, firefox
                     - necessario para post de conta privada/restrita
    --atualizar      atualiza a ferramenta (nuvem) e o yt-dlp, e sai
    --sem-atualizar  nao checa atualizacao nesta execucao
    --atualizar-ytdlp  atualiza so o yt-dlp e sai

A ferramenta se atualiza sozinha a partir de um link publico (Google Drive),
no maximo uma vez por dia. Ver atualizacao.py e atualizacao.json.
"""

import gzip
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

try:
    import atualizacao
except ImportError:          # ferramenta funciona sem o modulo de atualizacao
    atualizacao = None

BASE = "https://www.instagram.com/"
YTDLP_EXE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

# Header set de navegacao. NAO simplifique: sem Accept text/html +
# Sec-Fetch-Mode navigate o Instagram devolve a pagina sem os dados do post
# (blob JSON vazio) e a extracao falha em silencio. Medido em 2026-09-11.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-us,en;q=0.5",
    "Sec-Fetch-Mode": "navigate",
}

SJS_RE = re.compile(r"<script\b[^>]+\bdata-sjs>(\{.+?\})</script>")
SHORTCODE_RE = re.compile(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_BIN = os.path.join(AQUI, "bin")
# Nome do arquivo igual nos dois planos: <perfil> - <codigo>.mp4
MODELO_YTDLP = "%(channel,uploader_id)s - %(id)s.%(ext)s"


def log(msg):
    print(msg, flush=True)


# Ganchos que a interface grafica substitui para receber texto e progresso.
# No terminal ficam None e tudo sai no console, como sempre.
PROGRESSO = None


# ---------------------------------------------------------------- plano A


class Sessao:
    """Cookie jar + GET com os headers certos."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.aberta = False

    def get(self, url, extra=None, timeout=30):
        headers = dict(HEADERS)
        if extra:
            headers.update(extra)
        req = urllib.request.Request(url, headers=headers)
        resp = self.opener.open(req, timeout=timeout)
        dados = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            dados = gzip.decompress(dados)
        return resp.url, dados.decode("utf-8", "replace")

    def abrir(self):
        """Primeira visita: pega csrftoken/mid. Sem isso a pagina vem sem dados."""
        if not self.aberta:
            self.get(BASE)
            self.aberta = True


def achar_shortcode(texto):
    m = SHORTCODE_RE.search(texto.strip())
    return m.group(1) if m else None


def coletar_midias(html, codigo=None):
    """
    Varre os blobs <script data-sjs> da pagina.

    Devolve (midias, legenda, viu_dados). 'viu_dados' diz se a extracao
    funcionou: com ela True e midias vazio, o post e so de foto - e nao ha
    motivo para acionar o plano B.

    Busca recursiva por qualquer objeto que tenha 'video_versions' em vez de
    seguir um caminho fixo do JSON: o Instagram remexe a arvore com frequencia,
    mas o nome desse campo e estavel.

    ⚠️ A pagina NAO traz so o post pedido: vem junto a timeline do perfil.
    Medido em 2026-09-25 num post de churrascaria: **13 legendas na mesma
    pagina**, 12 delas de posts vizinhos. Daqui saem as duas regras abaixo.

    1. A legenda vale por CONTENCAO - e a do no que contem aquele video, nunca
       "a primeira legenda da pagina". Do jeito antigo acertava-se por sorte da
       ordem do JSON, e um post vizinho listado antes levaria a legenda errada
       para o .txt.
    2. Os videos ficam restritos a subarvore do post pedido (`codigo`). Sem
       isso, vizinho que seja reel entra na lista e e baixado junto, numerado
       como se fosse carrossel. Se nada casar com o codigo - estrutura mudou -
       cai para o que achou, que e o comportamento antigo.
    """
    do_alvo, quaisquer = [], []
    vistos_alvo, vistos_outros = set(), set()
    viu_dados = False

    def visitar(no, legenda, no_alvo):
        nonlocal viu_dados
        if isinstance(no, dict):
            if "image_versions2" in no or "xig_polaris_media" in no:
                viu_dados = True
            if codigo and no.get("code") == codigo:
                no_alvo = True
            cap = no.get("caption")
            if isinstance(cap, dict) and isinstance(cap.get("text"), str):
                texto = cap["text"].strip()
                if texto:
                    legenda = texto
            versoes = no.get("video_versions")
            if isinstance(versoes, list) and versoes:
                chave = no.get("pk") or no.get("id") or versoes[0].get("url", "")[:80]
                vistos = vistos_alvo if no_alvo else vistos_outros
                if chave not in vistos:
                    vistos.add(chave)
                    (do_alvo if no_alvo else quaisquer).append((no, legenda))
            for v in no.values():
                visitar(v, legenda, no_alvo)
        elif isinstance(no, list):
            for v in no:
                visitar(v, legenda, no_alvo)

    for blob in SJS_RE.findall(html):
        if not any(k in blob for k in ("video_versions", "image_versions2", "caption")):
            continue
        try:
            visitar(json.loads(blob), None, False)
        except (ValueError, RecursionError):
            continue

    achados = do_alvo or quaisquer
    return [m for m, _ in achados], (achados[0][1] if achados else None), viu_dados


def melhor_versao(midia):
    """
    Melhor versao oferecida.

    Criterio: area (width*height) quando o Instagram informa. Ele costuma
    omitir a dimensao no acesso deslogado e mandar so 'type' (101/102/103) -
    nesse caso desempata pelo menor type, que e a ordem de qualidade da API.
    Medido em 2026-09-11: as tres eram o MESMO arquivo, byte a byte.
    """
    validas = [
        v for v in midia["video_versions"]
        if isinstance(v, dict) and str(v.get("url", "")).startswith("http")
    ]
    if not validas:
        return None
    return max(
        validas,
        key=lambda v: (
            (v.get("width") or 0) * (v.get("height") or 0),
            -(v.get("type") if isinstance(v.get("type"), int) else 999),
        ),
    )


def dimensao(midia, versao):
    """Dimensao para exibir: da versao, ou a original do post."""
    largura = versao.get("width") or midia.get("original_width")
    altura = versao.get("height") or midia.get("original_height")
    return f"{largura or '?'}x{altura or '?'}"


def achar_autor(midia, html):
    for chave in ("user", "owner"):
        alvo = midia.get(chave)
        if isinstance(alvo, dict) and alvo.get("username"):
            return alvo["username"]
    m = re.search(r'"username":"([A-Za-z0-9._]+)"', html)
    return m.group(1) if m else "instagram"


def limpar_nome(texto):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", texto).strip(" .") or "video"


def baixar_arquivo(sessao, url, destino):
    parcial = destino + ".part"
    req = urllib.request.Request(
        url, headers={"User-Agent": HEADERS["User-Agent"], "Referer": BASE}
    )
    with sessao.opener.open(req, timeout=120) as resp, open(parcial, "wb") as saida:
        total = int(resp.headers.get("Content-Length") or 0)
        feito = 0
        while True:
            pedaco = resp.read(262144)
            if not pedaco:
                break
            saida.write(pedaco)
            feito += len(pedaco)
            if PROGRESSO:
                PROGRESSO(feito, total)
            elif total:
                print(f"\r    baixando... {feito * 100 // total:3d}%  "
                      f"({feito / 1048576:.1f} MB)", end="", flush=True)
            else:
                print(f"\r    baixando... {feito / 1048576:.1f} MB",
                      end="", flush=True)
    if not PROGRESSO:
        print("\r" + " " * 48 + "\r", end="")
    os.replace(parcial, destino)
    return os.path.getsize(destino)


def plano_a(sessao, url, pasta, salvar_legenda):
    """
    Tenta o extrator proprio.

    Devolve "ok" (baixou), "foto" (post sem video - nao insistir) ou
    "falha" (o plano B deve entrar).
    """
    codigo = achar_shortcode(url)
    log(f"  post {codigo}: lendo a pagina...")
    sessao.abrir()
    try:
        _, html = sessao.get(f"{BASE}p/{codigo}/")
    except urllib.error.HTTPError as e:
        log(f"    o Instagram respondeu HTTP {e.code}")
        return "falha"
    except OSError as e:
        log(f"    falha de rede: {e}")
        return "falha"

    midias, legenda, viu_dados = coletar_midias(html, codigo)
    if not midias:
        if viu_dados:
            log("  ! esse post nao tem video (so foto).")
            return "foto"
        log("    a pagina veio sem os dados do post")
        return "falha"

    autor = achar_autor(midias[0], html)
    baixados = 0
    for i, midia in enumerate(midias, start=1):
        versao = melhor_versao(midia)
        if not versao:
            continue
        sufixo = "" if len(midias) == 1 else f" ({i})"
        base = limpar_nome(f"{autor} - {codigo}{sufixo}")
        destino = os.path.join(pasta, base + ".mp4")
        if os.path.exists(destino):
            log(f"  = ja existe: {destino}")
            baixados += 1
            continue
        try:
            tamanho = baixar_arquivo(sessao, versao["url"], destino)
        except OSError as e:
            log(f"    falha ao baixar o mp4: {e}")
            continue
        log(f"  OK {destino}  ({tamanho / 1048576:.1f} MB, {dimensao(midia, versao)})")
        baixados += 1
        if salvar_legenda and legenda:
            with open(os.path.join(pasta, base + ".txt"), "w", encoding="utf-8") as f:
                f.write(legenda + "\n")

    if not baixados:
        return "falha"
    if legenda:
        primeira = legenda.splitlines()[0]
        log(f"  legenda: {primeira[:110]}{'...' if len(primeira) > 110 else ''}")
    return "ok"


# ---------------------------------------------------------------- plano B


def ytdlp_funciona(cmd):
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True,
                           text=True, timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def baixar_ytdlp_exe():
    """Baixa o executavel unico do yt-dlp do repositorio oficial no GitHub."""
    os.makedirs(PASTA_BIN, exist_ok=True)
    destino = os.path.join(PASTA_BIN, "yt-dlp.exe")
    parcial = destino + ".part"
    log("    baixando o yt-dlp do GitHub oficial (~18 MB)...")
    req = urllib.request.Request(YTDLP_EXE_URL, headers={"User-Agent": "insta-dl"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp, open(parcial, "wb") as saida:
            total = int(resp.headers.get("Content-Length") or 0)
            feito = 0
            primeiros = b""
            while True:
                pedaco = resp.read(262144)
                if not pedaco:
                    break
                if not primeiros:
                    primeiros = pedaco[:2]
                saida.write(pedaco)
                feito += len(pedaco)
                if total:
                    print(f"\r      {feito * 100 // total:3d}%  "
                          f"({feito / 1048576:.1f} MB)", end="", flush=True)
        print("\r" + " " * 40 + "\r", end="")
        if primeiros != b"MZ":
            log("    o que baixou nao e um executavel Windows; descartando.")
            os.remove(parcial)
            return None
    except OSError as e:
        print("")
        log(f"    nao consegui baixar o executavel: {e}")
        if os.path.exists(parcial):
            os.remove(parcial)
        return None
    os.replace(parcial, destino)
    if not ytdlp_funciona([destino]):
        log("    o executavel baixado nao rodou; descartando.")
        return None
    log(f"    yt-dlp instalado em {destino}")
    return [destino]


def instalar_ytdlp_pip():
    """Plano B do plano B: instala o pacote pip do yt-dlp numa venv local."""
    venv_py = os.path.join(AQUI, ".venv", "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        log("    criando ambiente Python local (.venv)...")
        r = subprocess.run([sys.executable, "-m", "venv", os.path.join(AQUI, ".venv")],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(venv_py):
            log("    nao consegui criar a .venv; tentando instalar no Python do sistema.")
            venv_py = sys.executable
    log("    instalando o pacote yt-dlp via pip...")
    r = subprocess.run([venv_py, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        saida = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        log("    pip falhou" + (": " + saida[-1] if saida else "."))
        return None
    exe = os.path.join(os.path.dirname(venv_py), "yt-dlp.exe")
    for cmd in ([exe], [venv_py, "-m", "yt_dlp"]):
        if (cmd[0] == venv_py or os.path.exists(exe)) and ytdlp_funciona(cmd):
            log("    yt-dlp instalado via pip.")
            return cmd
    return None


def garantir_ytdlp(instalar=True):
    """
    Devolve o comando do yt-dlp, instalando se preciso. None se nao houver jeito.

    Ordem: o que ja existe na maquina primeiro; instalar so em ultimo caso.
    """
    candidatos = [
        [os.path.join(AQUI, ".venv", "Scripts", "yt-dlp.exe")],
        [os.path.join(PASTA_BIN, "yt-dlp.exe")],
    ]
    achado = shutil.which("yt-dlp")
    if achado:
        candidatos.append([achado])
    candidatos.append([sys.executable, "-m", "yt_dlp"])

    for cmd in candidatos:
        if len(cmd) == 1 and not os.path.exists(cmd[0]):
            continue
        if ytdlp_funciona(cmd):
            return cmd

    if not instalar:
        return None
    log("  yt-dlp nao encontrado na maquina - instalando agora.")
    return baixar_ytdlp_exe() or instalar_ytdlp_pip()


def plano_b(urls, pasta, cookies=None, instalar=True):
    cmd = garantir_ytdlp(instalar=instalar)
    if not cmd:
        log("  ! nao consegui obter o yt-dlp (sem internet? proxy?).")
        log("    Manualmente: baixe " + YTDLP_EXE_URL)
        log("    e salve em " + os.path.join(PASTA_BIN, "yt-dlp.exe"))
        return False
    argv = cmd + ["-o", os.path.join(pasta, MODELO_YTDLP)]
    if cookies:
        argv += ["--cookies-from-browser", cookies]
    argv += list(urls)
    log("  plano B: yt-dlp assumindo...")
    return subprocess.call(argv) == 0


def atualizar_ytdlp():
    cmd = garantir_ytdlp(instalar=True)
    if not cmd:
        return 1
    if len(cmd) == 1 and cmd[0].lower().endswith("yt-dlp.exe") \
            and os.path.dirname(cmd[0]) == PASTA_BIN:
        return subprocess.call(cmd + ["-U"])
    py = os.path.join(AQUI, ".venv", "Scripts", "python.exe")
    if not os.path.exists(py):
        py = sys.executable
    return subprocess.call([py, "-m", "pip", "install", "--upgrade", "yt-dlp"])


# ---------------------------------------------------------------- orquestracao


def da_area_de_transferencia():
    try:
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=20,
        )
        return saida.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def reexecutar(argv):
    """Roda de novo com o codigo recem-atualizado, sem checar de novo."""
    log("  seguindo com a versao nova...")
    return subprocess.call(
        [sys.executable, os.path.abspath(__file__)] + list(argv) + ["--sem-atualizar"]
    )


def processar(sessao, url, pasta, salvar_legenda, usar_plano_b, cookies, so_plano_b):
    if not achar_shortcode(url):
        log(f"  ! nao parece um link de post/reel: {url}")
        return False

    if so_plano_b:
        return plano_b([url], pasta, cookies)

    resultado = plano_a(sessao, url, pasta, salvar_legenda)
    if resultado == "ok":
        return True
    if resultado == "foto":
        return False

    if not usar_plano_b:
        log("  ! o extrator proprio falhou e o plano B esta desligado (--sem-plano-b).")
        return False
    log("  o extrator proprio nao deu conta desse post.")
    if plano_b([url], pasta, cookies):
        return True
    if not cookies:
        log("  ! se o post for de conta privada, tente: --cookies chrome")
    return False


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    pasta = os.path.join(AQUI, "videos")
    salvar_legenda = False
    usar_plano_b = True
    so_plano_b = False
    cookies = None
    atualizar_agora = False
    checar_atualizacao = True
    urls = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-o", "--pasta"):
            i += 1
            if i >= len(argv):
                log("ERRO: -o precisa de uma pasta.")
                return 2
            pasta = argv[i]
        elif arg == "--legenda":
            salvar_legenda = True
        elif arg in ("--plano-b", "--via-ytdlp"):
            so_plano_b = True
        elif arg == "--sem-plano-b":
            usar_plano_b = False
        elif arg == "--cookies":
            i += 1
            if i >= len(argv):
                log("ERRO: --cookies precisa do navegador (chrome, edge, firefox).")
                return 2
            cookies = argv[i]
        elif arg == "--atualizar":
            atualizar_agora = True
        elif arg == "--sem-atualizar":
            checar_atualizacao = False
        elif arg == "--atualizar-ytdlp":
            return atualizar_ytdlp()
        elif arg in ("-h", "--help", "/?"):
            print(__doc__)
            return 0
        else:
            urls.append(arg)
        i += 1

    if atualizar_agora:
        if atualizacao:
            atualizacao.verificar(forcar=True, falar=True)
        else:
            log("  modulo de atualizacao ausente (atualizacao.py).")
        return atualizar_ytdlp()

    if checar_atualizacao and atualizacao:
        if atualizacao.verificar():
            return reexecutar(argv)

    if not urls:
        colado = da_area_de_transferencia()
        if achar_shortcode(colado):
            partes = colado.split()
            urls = [partes[0] if partes else colado]
            log(f"link da area de transferencia: {urls[0]}")
        else:
            log("Cole o link do post/reel como argumento, ou copie o link e rode de novo.")
            log("Exemplo: python baixar.py https://www.instagram.com/p/XXXXXXXXX/")
            return 2

    os.makedirs(pasta, exist_ok=True)
    log(f"destino: {pasta}")

    sessao = Sessao()
    falhas = 0
    for url in urls:
        if not processar(sessao, url, pasta, salvar_legenda,
                         usar_plano_b, cookies, so_plano_b):
            falhas += 1
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
