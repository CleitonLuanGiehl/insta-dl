#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
testes.py - suite de testes do insta-dl.

    python testes.py            roda tudo que nao depende de internet
    python testes.py --rede     inclui os testes que batem no Instagram e no GitHub
    python testes.py -v         detalhado

Cada teste aqui nasceu de um defeito real, e o nome diz qual. Nenhum e
decorativo: a suite existe porque esta sessao achou tres defeitos que build
verde nao pegava - legenda do post vizinho, pacote defasado do fonte, e o
manifest_id que nao era gravado localmente.
"""

import hashlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import baixar  # noqa: E402
import atualizacao  # noqa: E402

COM_REDE = "--rede" in sys.argv
REPO = "CleitonLuanGiehl/insta-dl"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/manifest.json"
# post publico usado nos testes de rede (reel de uma churrascaria)
POST = "C_22SZMOlXY"


# ----------------------------------------------------------------- ajudantes


def bloco_sjs(obj):
    """Imita um <script data-sjs> da pagina do Instagram."""
    return ('<script type="application/json" data-sjs>'
            + json.dumps(obj, ensure_ascii=False) + "</script>")


def pagina_com_vizinho(codigo_alvo="ALVO999999"):
    """
    Pagina onde o post VIZINHO vem antes do pedido.

    E a armadilha real: a pagina de um post traz a timeline do perfil junto
    (13 legendas numa pagina medida em 2026-09-25), e pegar "a primeira
    legenda" acerta por sorte da ordem do JSON.
    """
    vizinho = {"require": [{"polaris_ordered_timeline_connection": {"edges": [
        {"node": {
            "code": "VIZINHO111", "pk": "111",
            "caption": {"text": "legenda do post vizinho"},
            "image_versions2": {"candidates": []},
            "video_versions": [{"url": "https://cdn/vizinho.mp4", "type": 101}],
        }}]}}]}
    alvo = {"require": [{"xig_polaris_media": {"if_not_gated_logged_out": {
        "code": codigo_alvo, "pk": "999",
        "caption": {"text": "legenda do post pedido"},
        "image_versions2": {"candidates": []},
        "original_width": 1080, "original_height": 1920,
        "video_versions": [{"url": "https://cdn/alvo.mp4", "type": 101}],
    }}}]}
    return bloco_sjs(vizinho) + bloco_sjs(alvo)


class Servidor(http.server.BaseHTTPRequestHandler):
    rotas = {}

    def do_GET(self):
        chave = self.path.split("?")[0]
        if chave not in Servidor.rotas:
            self.send_error(404)
            return
        tipo, corpo = Servidor.rotas[chave]
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


class ComServidor(unittest.TestCase):
    """Base para quem precisa de um servidor local imitando a hospedagem."""

    @classmethod
    def setUpClass(cls):
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), Servidor)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def instalar(self, versao="1.0.0", com_exe=False):
        """Cria uma 'instalacao' isolada e devolve (pasta, modulo)."""
        pasta = tempfile.mkdtemp(prefix="teste-")
        self.addCleanup(shutil.rmtree, pasta, True)
        for nome in ("baixar.py", "atualizacao.py", "insta-dl.pyw",
                     "COMECE-AQUI.html", "README.md"):
            shutil.copy2(os.path.join(AQUI, nome), pasta)
        if com_exe:
            # layout portatil: fontes em app\, executavel na pasta de cima
            app = os.path.join(pasta, "app")
            os.makedirs(app, exist_ok=True)
            for nome in os.listdir(pasta):
                if nome.endswith((".py", ".pyw")):
                    shutil.move(os.path.join(pasta, nome), app)
            with open(os.path.join(pasta, "insta-dl.exe"), "wb") as f:
                f.write(b"MZ finge que sou o executavel")
            with open(os.path.join(pasta, "COMECE-AQUI.html"), "w",
                      encoding="utf-8") as f:
                f.write("<html>VELHO</html>")
            pasta_mod = app
        else:
            pasta_mod = pasta
        with open(os.path.join(pasta_mod, "versao.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump({"versao": versao}, f, ensure_ascii=False, indent=2)
        with open(os.path.join(pasta_mod, "atualizacao.json"), "w",
                  encoding="utf-8") as f:
            json.dump({}, f)
        os.makedirs(os.path.join(pasta, "videos"), exist_ok=True)
        with open(os.path.join(pasta, "videos", "meu.mp4"), "wb") as f:
            f.write(b"nao me apague")

        caminho = os.path.join(pasta_mod, "atualizacao.py")
        nome_mod = "atz_" + os.path.basename(pasta)
        spec = importlib.util.spec_from_file_location(nome_mod, caminho)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[nome_mod] = mod
        spec.loader.exec_module(mod)
        return pasta, mod

    def apontar(self, mod, url):
        with open(mod.ARQ_CONFIG, "w", encoding="utf-8") as f:
            json.dump({"manifest_url": url}, f)

    def pacote(self, versao, marcador=b"NOVO", html_novo=False):
        """Monta um pacote de atualizacao, como o empacotar.py faria."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for nome in ("baixar.py", "atualizacao.py", "insta-dl.pyw",
                         "README.md"):
                dados = open(os.path.join(AQUI, nome), "rb").read()
                if nome == "baixar.py":
                    dados = dados.replace(b"# -*- coding: utf-8 -*-",
                                          b"# -*- coding: utf-8 -*-\n# " + marcador)
                z.writestr(nome, dados)
            z.writestr("COMECE-AQUI.html",
                       "<html>NOVO</html>" if html_novo else "<html>x</html>")
            z.writestr("versao.json", json.dumps({"versao": versao}))
            z.writestr("atualizacao.json", json.dumps({}))
        return buf.getvalue()


# ----------------------------------------------------------------- a legenda


class TestLegenda(unittest.TestCase):
    """
    Achado de 2026-09-25: a legenda nao estava amarrada ao video.
    O usuario desconfiou do resultado e o defeito estava um degrau atras -
    estava acertando por sorte da ordenacao do JSON.
    """

    def test_legenda_e_do_post_pedido_nao_do_vizinho(self):
        _, legenda, _ = baixar.coletar_midias(pagina_com_vizinho(), "ALVO999999")
        self.assertEqual(legenda, "legenda do post pedido")

    def test_baixa_so_o_video_do_post_pedido(self):
        midias, _, _ = baixar.coletar_midias(pagina_com_vizinho(), "ALVO999999")
        self.assertEqual(len(midias), 1)
        self.assertEqual(midias[0].get("code"), "ALVO999999")

    def test_sem_o_codigo_o_vizinho_contamina(self):
        """Documenta POR QUE o codigo do post e obrigatorio."""
        midias, legenda, _ = baixar.coletar_midias(pagina_com_vizinho())
        self.assertEqual(len(midias), 2)
        self.assertEqual(legenda, "legenda do post vizinho")

    def test_carrossel_herda_a_legenda_do_pai(self):
        carrossel = {"require": [{"xig_polaris_media": {"if_not_gated_logged_out": {
            "code": "CARR000000",
            "caption": {"text": "legenda do carrossel"},
            "image_versions2": {"candidates": []},
            "carousel_media": [
                {"pk": "a1", "video_versions": [{"url": "https://c/1.mp4", "type": 101}]},
                {"pk": "a2", "video_versions": [{"url": "https://c/2.mp4", "type": 101}]},
            ]}}}]}
        midias, legenda, _ = baixar.coletar_midias(
            bloco_sjs(carrossel), "CARR000000")
        self.assertEqual(len(midias), 2)
        self.assertEqual(legenda, "legenda do carrossel")

    def test_post_so_de_foto_e_reconhecido(self):
        """viu_dados=True com zero video significa foto - nao aciona o plano B."""
        foto = {"require": [{"image_versions2": {"candidates": [{"url": "x"}]},
                             "media_type": 1,
                             "caption": {"text": "foto"}}]}
        midias, _, viu = baixar.coletar_midias(bloco_sjs(foto), "QUALQUER")
        self.assertEqual(midias, [])
        self.assertTrue(viu)

    def test_melhor_versao_desempata_por_type(self):
        """Sem dimensao, o menor 'type' e a ordem de qualidade da API."""
        midia = {"video_versions": [
            {"url": "https://c/103.mp4", "type": 103},
            {"url": "https://c/101.mp4", "type": 101},
            {"url": "https://c/102.mp4", "type": 102}]}
        self.assertEqual(baixar.melhor_versao(midia)["type"], 101)

    def test_melhor_versao_prefere_area_quando_informada(self):
        midia = {"video_versions": [
            {"url": "https://c/p.mp4", "type": 101, "width": 480, "height": 640},
            {"url": "https://c/g.mp4", "type": 103, "width": 1080, "height": 1920}]}
        self.assertEqual(baixar.melhor_versao(midia)["width"], 1080)


class TestEnderecos(unittest.TestCase):
    def test_formas_de_url_aceitas(self):
        casos = {
            "https://www.instagram.com/p/ABC123/": "ABC123",
            "https://www.instagram.com/reel/ABC_1-2/": "ABC_1-2",
            "https://www.instagram.com/perfil/reel/XYZ/": "XYZ",
            "https://instagram.com/p/QWE/?igsh=abc": "QWE",
            "https://www.instagram.com/tv/AA1/": "AA1",
        }
        for url, esperado in casos.items():
            self.assertEqual(baixar.achar_shortcode(url), esperado, url)

    def test_story_e_link_qualquer_nao_sao_aceitos(self):
        self.assertIsNone(baixar.achar_shortcode(
            "https://www.instagram.com/stories/foo/123/"))
        self.assertIsNone(baixar.achar_shortcode("https://example.com/nada"))

    def test_headers_de_navegacao_obrigatorios(self):
        """
        Sem este header set o Instagram devolve HTTP 200 com a pagina VAZIA de
        dados, e a extracao falha em silencio. Medido em 2026-09-11.
        """
        self.assertIn("text/html", baixar.HEADERS["Accept"])
        self.assertEqual(baixar.HEADERS["Sec-Fetch-Mode"], "navigate")


# ------------------------------------------------------------- a atualizacao


class TestComparacaoDeVersao(unittest.TestCase):
    def test_compara_por_numero_nao_por_texto(self):
        self.assertEqual(atualizacao.comparar("1.0.10", "1.0.9"), 1)

    def test_maior_menor_igual(self):
        self.assertEqual(atualizacao.comparar("1.1.0", "1.0.0"), 1)
        self.assertEqual(atualizacao.comparar("1.0.0", "1.1.0"), -1)
        self.assertEqual(atualizacao.comparar("2.0.0", "2.0.0"), 0)

    def test_segmento_ausente_vale_zero(self):
        self.assertEqual(atualizacao.comparar("1.0", "1.0.0"), 0)


class TestSegurancaDoPacote(unittest.TestCase):
    def monta(self, nomes):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for nome in nomes:
                z.writestr(nome, "x")
        return buf.getvalue()

    def test_recusa_caminho_para_fora_da_pasta(self):
        with self.assertRaises(RuntimeError):
            atualizacao.conferir_zip(self.monta(["../fora.txt"]))

    def test_recusa_caminho_absoluto(self):
        with self.assertRaises(RuntimeError):
            atualizacao.conferir_zip(self.monta(["C:/windows/x.txt"]))

    def test_recusa_pasta_no_pacote(self):
        with self.assertRaises(RuntimeError):
            atualizacao.conferir_zip(self.monta(["sub/x.txt"]))

    def test_aceita_pacote_plano(self):
        self.assertEqual(
            sorted(atualizacao.conferir_zip(self.monta(["a.py", "b.json"]))),
            ["a.py", "b.json"])

    def test_recusa_zip_corrompido(self):
        with self.assertRaises(Exception):
            atualizacao.conferir_zip(b"isto nao e um zip")


class TestFluxoDeAtualizacao(ComServidor):
    def rotas(self, versao, dados, sha=None, incluir_sha=True):
        manifesto = {"versao": versao, "pacote_url": self.base + "/p.zip"}
        if incluir_sha:
            manifesto["sha256"] = sha or hashlib.sha256(dados).hexdigest()
        Servidor.rotas = {
            "/manifest.json": ("application/json",
                               json.dumps(manifesto).encode()),
            "/p.zip": ("application/zip", dados),
        }

    def test_aplica_e_preserva_os_videos(self):
        pasta, mod = self.instalar("1.0.0")
        dados = self.pacote("1.1.0")
        self.rotas("1.1.0", dados)
        self.apontar(mod, self.base + "/manifest.json")
        self.assertEqual(mod.verificar(forcar=True), "1.1.0")
        self.assertEqual(mod.ler_versao(), "1.1.0")
        self.assertIn(b"NOVO", open(os.path.join(mod.AQUI, "baixar.py"), "rb").read())
        self.assertEqual(
            open(os.path.join(pasta, "videos", "meu.mp4"), "rb").read(),
            b"nao me apague")

    def test_recusa_pacote_com_sha256_diferente(self):
        _, mod = self.instalar("1.0.0")
        dados = self.pacote("1.1.0")
        corrompido = bytearray(dados)
        corrompido[-5] ^= 0xFF
        self.rotas("1.1.0", bytes(corrompido),
                   sha=hashlib.sha256(dados).hexdigest())
        self.apontar(mod, self.base + "/manifest.json")
        self.assertIsNone(mod.verificar(forcar=True))
        self.assertEqual(mod.ler_versao(), "1.0.0")

    def test_nao_aplica_sem_sha256_no_manifest(self):
        _, mod = self.instalar("1.0.0")
        self.rotas("1.1.0", self.pacote("1.1.0"), incluir_sha=False)
        self.apontar(mod, self.base + "/manifest.json")
        self.assertIsNone(mod.verificar(forcar=True))
        self.assertEqual(mod.ler_versao(), "1.0.0")

    def test_nao_faz_downgrade(self):
        _, mod = self.instalar("1.5.0")
        self.rotas("0.9.0", self.pacote("0.9.0"))
        self.apontar(mod, self.base + "/manifest.json")
        self.assertIsNone(mod.verificar(forcar=True))
        self.assertEqual(mod.ler_versao(), "1.5.0")

    def test_sem_link_configurado_fica_silencioso(self):
        _, mod = self.instalar("1.0.0")
        self.assertIsNone(mod.verificar(forcar=True))

    def test_offline_nao_levanta_excecao(self):
        _, mod = self.instalar("1.0.0")
        self.apontar(mod, "http://127.0.0.1:9/manifest.json")
        self.assertIsNone(mod.verificar(forcar=True))
        self.assertEqual(mod.ler_versao(), "1.0.0")

    def test_checa_no_maximo_uma_vez_por_dia(self):
        _, mod = self.instalar("1.0.0")
        self.rotas("0.9.0", self.pacote("0.9.0"))
        self.apontar(mod, self.base + "/manifest.json")
        mod.verificar(forcar=True)
        self.assertTrue(os.path.exists(mod.ARQ_SELO))
        self.assertFalse(mod.deve_checar(forcar=False))
        self.assertTrue(mod.deve_checar(forcar=True))

    def test_no_portatil_o_html_vai_para_a_raiz(self):
        """
        No layout portatil os fontes ficam em app\\ e o COMECE-AQUI.html mora
        na pasta de cima. Sem este desvio a atualizacao gravaria uma copia
        dentro de app\\ e a pessoa seguiria abrindo a versao velha.
        """
        pasta, mod = self.instalar("1.0.0", com_exe=True)
        self.assertEqual(mod.RAIZ, pasta)
        dados = self.pacote("1.1.0", html_novo=True)
        self.rotas("1.1.0", dados)
        self.apontar(mod, self.base + "/manifest.json")
        self.assertEqual(mod.verificar(forcar=True), "1.1.0")
        self.assertIn("NOVO", open(os.path.join(pasta, "COMECE-AQUI.html"),
                                   encoding="utf-8").read())
        self.assertFalse(os.path.exists(
            os.path.join(pasta, "app", "COMECE-AQUI.html")))

    def test_pagina_de_login_da_mensagem_util(self):
        """
        Arquivo que exige login devolve a TELA DE ENTRADA, que tambem tem um
        <form>. Seguir esse formulario produzia um ValueError ilegivel.
        """
        Servidor.rotas = {"/manifest.json": ("text/html", (
            '<html><body><form action="/v3/signin/identifier" method="post">'
            '<input type="hidden" name="continue" value="x"></form>'
            'ServiceLogin</body></html>').encode())}
        with self.assertRaises(RuntimeError) as caso:
            atualizacao.baixar_url(self.base + "/manifest.json")
        self.assertIn("login", str(caso.exception).lower())

    def test_cota_estourada_da_mensagem_util(self):
        Servidor.rotas = {"/manifest.json": ("text/html", (
            b"<html>Too many users have viewed or downloaded this file"
            b" recently.</html>"))}
        with self.assertRaises(RuntimeError) as caso:
            atualizacao.baixar_url(self.base + "/manifest.json")
        self.assertIn("cota", str(caso.exception).lower())


class TestPacoteConstruido(unittest.TestCase):
    """
    Achado de 2026-10-02: uma release foi montada ANTES da ultima correcao e
    publicada defasada do fonte. Build verde, testes verdes, artefato errado.
    """

    def pacote_mais_novo(self):
        dist = os.path.join(AQUI, "dist")
        if not os.path.isdir(dist):
            return None
        cands = [os.path.join(dist, n) for n in os.listdir(dist)
                 if n.startswith("insta-dl-") and n.endswith(".zip")
                 and "portatil" not in n]
        return max(cands, key=os.path.getmtime) if cands else None

    def test_o_pacote_construido_nao_esta_defasado_do_fonte(self):
        caminho = self.pacote_mais_novo()
        if not caminho:
            self.skipTest("nenhum pacote em dist/ - rode o empacotar.py")
        divergentes = []
        with zipfile.ZipFile(caminho) as z:
            for nome in ("baixar.py", "atualizacao.py", "insta-dl.pyw",
                         "COMECE-AQUI.html", "README.md"):
                with open(os.path.join(AQUI, nome), "rb") as f:
                    if z.read(nome) != f.read():
                        divergentes.append(nome)
        self.assertEqual(divergentes, [], f"{os.path.basename(caminho)} difere "
                                          f"do fonte - reconstrua antes de publicar")


# ------------------------------------------------------------------ com rede


@unittest.skipUnless(COM_REDE, "use --rede")
class TestNaRede(unittest.TestCase):
    def test_manifest_publicado_e_legivel_sem_credencial(self):
        req = urllib.request.Request(RAW, headers={"User-Agent": "insta-dl"})
        with urllib.request.urlopen(req, timeout=30) as r:
            manifesto = json.loads(r.read().decode())
        self.assertRegex(str(manifesto.get("versao")), r"^\d+\.\d+\.\d+$")
        self.assertEqual(len(str(manifesto.get("sha256"))), 64)
        self.assertTrue(str(manifesto["pacote_url"]).startswith("https://"))

    def test_pacote_publicado_baixa_e_o_sha256_confere(self):
        req = urllib.request.Request(RAW, headers={"User-Agent": "insta-dl"})
        with urllib.request.urlopen(req, timeout=30) as r:
            manifesto = json.loads(r.read().decode())
        req = urllib.request.Request(manifesto["pacote_url"],
                                     headers={"User-Agent": "insta-dl"})
        with urllib.request.urlopen(req, timeout=180) as r:
            dados = r.read()
        self.assertEqual(hashlib.sha256(dados).hexdigest(), manifesto["sha256"])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(dados)))

    def test_extrai_um_post_publico_de_verdade(self):
        sessao = baixar.Sessao()
        sessao.abrir()
        _, html = sessao.get("https://www.instagram.com/p/%s/" % POST)
        midias, legenda, viu = baixar.coletar_midias(html, POST)
        self.assertTrue(viu, "a pagina veio sem os dados do post")
        self.assertEqual(len(midias), 1)
        self.assertEqual(midias[0].get("code"), POST)
        propria = midias[0].get("caption", {}).get("text", "").strip()
        self.assertEqual(legenda, propria)
        self.assertTrue(baixar.melhor_versao(midias[0])["url"].startswith("http"))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.argv = [a for a in sys.argv if a != "--rede"]
    unittest.main(verbosity=2)
