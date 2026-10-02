# insta-dl

Ferramenta local para baixar video de post/reel do Instagram, sem passar por
site intermediario. Distribuida por link do Google Drive e com auto atualizacao.

## Dois pacotes, dois papeis

| Pacote | Como se gera | Tamanho | Para que serve |
|---|---|---|---|
| `insta-dl-portatil-X.Y.Z.zip` | `python montar-portatil.py` | ~14 MB | **primeiro download** do colega: traz o Python dentro |
| `insta-dl-X.Y.Z.zip` | `python empacotar.py` | ~20 KB | **atualizacoes**: troca so os arquivos de `app\` |

O manifest aponta para o pacote PEQUENO. O runtime so viaja no primeiro
download; atualizacao nao rebaixa 14 MB.

## Como o colega usa

Descompacta e da **duplo clique no `insta-dl.exe`**. Nao instala nada, nao pede
senha de administrador, nao escreve no registro. Desinstalar = apagar a pasta.

A pasta que ele ve:

```
insta-dl  COMECE-AQUI.html     <- a orientacao, abre no navegador
  insta-dl.exe         <- duplo clique
  app\                 <- o codigo (texto legivel)
  videos\              <- o que foi baixado
  sistema\             <- o Python que vem junto
  python313.dll, vcruntime140.dll, ...
```

## Por que NAO ha .cmd, .bat ou .exe empacotado

Script solto (`.cmd`/`.bat`/`.ps1`) e o formato que as pessoas aprenderam a
nao clicar - e num parque com antivirus corporativo (aqui e Trend Micro) ele
chama atencao a toa. Um `.exe` gerado por PyInstaller seria PIOR: binario
*packed* sem assinatura e exatamente o perfil que esses agentes marcam, e o
alerta cai no painel do TI.

A saida: **o `insta-dl.exe` nao e um binario nosso**. E o `pythonw.exe` do
pacote *embeddable* oficial do python.org, apenas renomeado - e renomear
preserva a assinatura Authenticode. Medido em 2026-09-15:

```
insta-dl.exe -> Valid | CN=Python Software Foundation
```

Qualquer pessoa confere: botao direito -> Propriedades -> Assinaturas Digitais.

Um interpretador sozinho abriria o REPL, nao a nossa janela. Quem liga uma
coisa na outra e o `sistema\sitecustomize.py`: com `import site` ligado no
`python313._pth`, o proprio Python carrega esse arquivo no startup, e dali
chamamos a janela. Por isso o executavel funciona **sem receber argumento**.

### O que o pacote embeddable NAO traz

Interface grafica. Ele vem com 34 arquivos e zero tkinter/tcl - medido. O
`montar-portatil.py` copia `_tkinter.pyd`, `tcl86t.dll`, `tk86t.dll`,
`zlib1.dll`, `Lib	kinter` e `tcl\` de uma instalacao normal do Python (de
proposito **nao** a da Microsoft Store: ACL restritiva viaja junto na copia).
Como a biblioteca do Tcl acaba em `sistema	cl`, o `sitecustomize` aponta
`TCL_LIBRARY`/`TK_LIBRARY` para la, senao o tkinter sobe com
"Can't find a usable init.tcl".

## Os dois planos de download

**Plano A - extrator proprio.** So biblioteca padrao. Visita o
`instagram.com`, pega os cookies de sessao, le os blocos JSON
`<script data-sjs>` embutidos na propria pagina, acha `video_versions` e baixa
o mp4 do CDN (fbcdn). E o mesmo caminho do navegador.

**Plano B - yt-dlp, automatico.** Se o plano A falhar, cai sozinho no yt-dlp,
sem flag e sem perguntar; se ele nao existir, se instala (executavel unico do
GitHub oficial, ou pacote pip). Post so de foto **nao** aciona o plano B.

## Auto atualizacao

A cada execucao (no maximo 1x por dia) le um `manifest.json` publico no Drive.
Versao maior que a local -> baixa o pacote pequeno, **confere o SHA-256**,
troca os arquivos de `app\`. Sem internet ou sem link configurado: silenciosa,
a ferramenta segue funcionando.

Garantias, todas com teste:

- sem sha256 no manifest, **nao aplica**; pacote que nao casa e descartado
- **nao faz downgrade**
- **`videos\` nunca e tocado**; entradas com `..`, caminho absoluto ou pasta
  sao recusadas antes de extrair
- arquivos de `NA_RAIZ` (o `COMECE-AQUI.html`) vao para a raiz do portatil, nao
  para dentro de `app\`
- trocar `.py` com a ferramenta ABERTA e seguro: o Python le o fonte e fecha,
  nao trava o arquivo (medido em 2026-09-15)

## Publicar uma versao nova

```
python empacotar.py --versao 1.2.0 --notas "o que mudou"
python montar-portatil.py --versao 1.2.0     (so quando quiser refazer o zip grande)
```

No Drive:

1. sobe o `.zip` pequeno como arquivo novo, compartilha como *qualquer pessoa
   com o link*, copia o ID
2. `python empacotar.py --pacote-id <ID>`
3. no `manifest.json` que **ja existe**: botao direito -> *Gerenciar versoes* ->
   *Enviar nova versao* -> escolhe `dist\manifest.json`

**A regra que quebra tudo se ignorada:** o ID de um arquivo do Drive muda a
cada upload novo. O `manifest.json` e o unico link que as maquinas dos colegas
conhecem. Por isso ele se atualiza por *"Enviar nova versao"* (mantem o ID) e
**nunca** por upload novo.

O zip pequeno e reproduzivel: mesmo conteudo gera o mesmo sha256.

### Quem pode editar esses dois arquivos publica codigo

A auto atualizacao roda o que estiver no pacote, em toda maquina que tem a
ferramenta. O sha256 protege contra download corrompido, nao contra manifest
trocado. Deixe os dois com edicao restrita a voce e link so de leitura.

## Detalhes que quebram facil (nao "simplifique")

- **Headers do Instagram.** Sem `Accept: text/html...` e `Sec-Fetch-Mode:
  navigate`, vem HTTP 200 com a pagina **sem os dados do post** - falha em
  silencio, parecendo "post sem video". Medido: header generico = 0
  ocorrencias de `video_versions`; header de navegacao = 1 bloco com as 3 URLs.
- **`python313._pth` e o arquivo que vale.** Um `._pth` com o nome do
  executavel (`insta-dl._pth`) foi ignorado; quem foi lido foi o
  `python313._pth`. Medido em 2026-09-15 - se editar o errado, o `sys.path`
  sai sem `Lib`, o `sitecustomize` nao carrega e o exe abre o REPL invisivel.
- **Sem console nao existe mensagem de erro.** Qualquer excecao no startup do
  portatil vai para `erro.log` ao lado do executavel. Sem isso, falha = "clicou
  e nao aconteceu nada".
- **Deteccao de Python nunca por `where python`**: o stub da Microsoft Store
  esta no PATH mesmo sem Python instalado (sai com 9009).

## Limites

- Post publico sai no plano A. Conta privada/restrita exige `--cookies chrome`.
- Post so de foto nao tem video: avisa e nao aciona o plano B.
- Story nao e coberto (link nao tem o formato `/p/` ou `/reel/`).
- Carrossel com varios videos: baixa todos, numerados ` (1)`, ` (2)`...
- O conteudo continua sendo de quem publicou; ao repostar, credite o perfil de
  origem (esta no nome do arquivo).
