# App de teste — Rececionista Virtual (Paraíso Villas & Spa)

Aplicação **independente** para validar o fluxo conversacional e o envio por
**WhatsApp Web**, **sem qualquer ligação ao Newhotel**.

> Os preços e a disponibilidade vêm de um **ficheiro CSV local** que a receção
> mantém (`data/teste_app/disponibilidade.csv`), quando esse modo está ligado na
> Configuração. Com esse modo desligado a app **não apresenta valores**: regista
> o pedido e diz ao cliente que a receção responde ao abrir. Em nenhum dos casos
> a app abre um navegador para consultar disponibilidade. O `fake_pms.py`
> (preços fictícios e determinísticos) continua a existir para os testes.

---

## 1. Requisitos

- Windows 10/11 com Python 3.11 ou superior (python.org, com "Add to PATH").
- Para envio real: Google Chrome instalado.

```bat
pip install playwright
playwright install chromium
```

Nada mais é necessário: a interface usa Tkinter (incluído no Python).

Opcional, para compreender mensagens ambíguas: **Ollama** com um modelo local
(secção 3). Sem ele a app funciona apenas com as regras.

> **Atenção ao `python` do PATH.** Se tiver o LibreOffice Portable ou o XAMPP
> instalados, o primeiro `python.exe` do PATH pode ser o Python embutido deles
> (3.8, sem Tkinter). Nesse caso o arranque falha com
> `ModuleNotFoundError: No module named 'tkinter'`.
> O `run_app.bat` já trata disto: testa cada candidato e só aceita Python 3.10+
> **com** Tkinter, mostrando no fim qual vai usar. Se arrancar a app à mão, use
> `py -3 -m rececionista` (o launcher oficial aponta para o Python correto) em
> vez de `python -m rececionista`.

## 2. Arrancar

```bat
run_app.bat
```

ou

```bat
py -3 -m rececionista
```

## 3. Testar sem WhatsApp (recomendado para começar)

No separador **Simulador**:

1. Confirme o número em "Número do cliente" (por defeito `+351900000001`,
   que está na lista de autorizados).
   O número pode ser escrito em qualquer formato — `+351 912 345 678`,
   `00351912345678` ou `912345678` são o mesmo contacto. Se aparecer
   *"Mensagem não respondida (número não autorizado)"*, a mensagem mostra o
   número tal como foi interpretado e o separador **Configuração** tem a lista
   de autorizados.
2. Use os botões de exemplo (Disponibilidade PT/EN/ES/FR, Sem disponibilidade,
   Fotos, Spa, Quero reservar, Áudio) ou escreva livremente.
3. As respostas aparecem no painel; os pedidos de reserva vão para o separador
   **Pedidos de reserva** com estado `pending`.

Fluxo completo a experimentar:

```
Disponibilidade 12/12/2026 a 15/12/2026 para 2 pessoas com pequeno-almoço
→ com o interruptor LIGADO: preço por noite + total (aviso "estou a verificar")
→ com o interruptor DESLIGADO: pedido registado + "a receção responde ao abrir"

Quero reservar
→ pede nome e email

O meu nome é Ana Silva e o meu email é ana.silva@example.com
→ resumo do pedido + "NÃO está confirmado"
```

### De onde vêm os preços: o interruptor

Não há navegador, não há scraping do Newhotel. Em **Configuração →
Disponibilidade e preços (sem navegador)** há uma única escolha:

| Interruptor | O que a app faz |
|---|---|
| **Ligado** | Lê `data/teste_app/disponibilidade.csv` (ou o caminho indicado) e responde com preços e disponibilidade reais desse ficheiro. |
| **Desligado** (por defeito) | Não consulta nada. Responde *"registei o seu pedido para 20/12 a 23/12 … assim que a receção abrir alguém responde"* e marca a conversa para intervenção humana. Nunca apresenta valores. |

O cabeçalho da janela mostra sempre qual está ativo: `PREÇOS: FICHEIRO LOCAL`
(verde) ou `SEM PREÇOS · RECEÇÃO RESPONDE` (amarelo).

O ficheiro é um CSV que se abre no Excel:

```
data,villa,disponivel,preco_bb,preco_ro
2026-12-20,T0,sim,199.00,175.00
2026-12-20,T1,sim,259.00,229.00
2026-12-20,T2,nao,,
```

- `data` aceita `2026-12-20`, `20/12/2026` ou `20-12-2026`.
- `disponivel` aceita `sim`/`nao`, `yes`/`no`, `1`/`0`.
- Os preços aceitam `259.50` ou `259,50 €`. Separador `,` ou `;`.
- Uma noite esgotada (`nao`) ou sem preço para o regime pedido **não é
  inventada** — essa villa simplesmente não é oferecida.
- Se o ficheiro **não cobrir as datas pedidas**, a app não diz que está
  esgotado: diz que não consegue confirmar e passa o pedido à receção.
- O ficheiro é relido automaticamente quando é alterado, sem reiniciar a app.

O botão **Criar ficheiro de exemplo** gera 45 dias a partir de hoje (com alguns
fins de semana esgotados, para testar as alternativas). **Abrir ficheiro** abre-o
no Excel/editor associado.

### Compreensão com modelo local (Ollama) — opcional

A app funciona **sem** modelo nenhum. As regras resolvem a maioria das
mensagens; o modelo local entra apenas quando as regras devolvem `unclear`.

```
mensagem → regras → percebeu? → sim: responde (rápido, ~0 s)
                                não: Ollama interpreta → entidades somadas
                                     → motor responde com as mesmas garantias
```

O modelo **só compreende**: devolve intenção, datas, hóspedes, regime,
tipologia, nome e email. Preços, disponibilidade e confirmações continuam nos
templates — o modelo não escreve números nem confirma reservas. Se o Ollama
estiver desligado, demorar demasiado ou devolver lixo, a app responde como
antes, por regras.

#### 1. Instalar o Ollama (Windows)

1. Descarregue o instalador em **ollama.com/download** e execute-o.
2. Fica a correr em segundo plano (ícone junto ao relógio) em
   `http://localhost:11434`. Não precisa de conta nem de Internet depois do
   download dos modelos.
3. Abra o **Prompt de Comando** e descarregue o modelo:

```bat
ollama pull qwen3:4b
```

São ~2,6 GB (uma vez). Depois confirme:

```bat
ollama list
ollama run qwen3:4b "Bom dia, quanto custa uma villa?"
```

Saia com `/bye`. Este `run` inicial carrega o modelo para memória — a primeira
chamada da app é sempre mais lenta.

| Modelo | RAM em uso | Quando escolher |
|---|---|---|
| `qwen3:4b` (por defeito) | ~3,5 GB | 8 GB de RAM ou mais; melhor compreensão em PT |
| `qwen3:1.7b` | ~1,8 GB | PC com pouca RAM; mais rápido, menos preciso |
| `qwen2.5:3b-instruct-q4_K_M` | ~2,5 GB | alternativa rápida se o `qwen3` for lento demais |

Num i5 sem GPU, um 4B responde em ~8–25 s por mensagem. É por isso que as
regras correm primeiro: o modelo só é chamado no que é genuinamente ambíguo.

> **Nota sobre o Qwen3:** o Qwen3 "pensa" antes de responder, o que nesta
tarefa só acrescenta espera e estraga o JSON. A app desliga esse modo
automaticamente (`think: false` no pedido ao Ollama) quando o modelo começa por
`qwen3`. Se quiser usar outro modelo, escreva o nome tal como aparece no
`ollama list`. Isto exige um Ollama atualizado (0.9 ou mais recente); se o
Ollama for antigo, atualize-o a partir do site.

#### 2. Configurar na app

**Configuração → Compreensão com modelo local (Ollama)**:

1. Marque **"Usar o modelo quando as regras não percebem a mensagem"**.
2. Confirme o endereço (`http://localhost:11434`) e o modelo (tem de ser o nome
   exato do `ollama list`).
3. **Espera**: 45 s por defeito; aumente para 90–120 se o PC for lento.
4. **Testar ligação** — diz se o Ollama responde e se o modelo está
   descarregado, sem precisar de guardar.
5. **Guardar**.

#### O que muda na conversa

- Frases indiretas — *"andava a ver se dava para ficar aí um fim de semana"* —
  passam a ser reconhecidas como pedido de disponibilidade.
- Entidades em língua livre — *"somos um casal com um bebé"*, *"fim do ano"*,
  *"duas noites"* — chegam ao motor.
- Referências à conversa anterior — *"e se ficássemos só duas noites?"* —
  reaproveitam a entrada do último orçamento **sem** chamar o modelo (é lógica
  de sessão, determinística).
- Respostas curtas a uma pergunta da app — depois de *"Quantas noites pretende
  ficar?"*, a resposta **completa** o pedido com as datas já dadas, em vez de
  voltar ao início. Respostas aceites: `2`, `2 noites`, `duas noites`,
  `3-4 noites` (usa 3, o limite inferior), `cerca de 4`, `fim de semana` (2),
  `uma semana` (7), `duas semanas` (14) e `uns dias` (2). Isto é lógica de
  sessão, não do modelo: a consulta pendente vale 30 minutos.
- Se responder com **datas novas** (*"afinal de 25 a 27 de dezembro"*), essas
  datas ganham às guardadas — só as noites são aproveitadas da consulta
  pendente.
- **O contexto sobrevive a fechar a app.** O último orçamento e a consulta
  pendente ficam na tabela `sessions` da base de dados, por número: reabrir a
  app e responder *"e se fossem só duas noites?"* continua a funcionar. O
  contexto é retomado até **24 horas** depois da última mensagem; passado esse
  prazo é descartado. **Limpar** a conversa no simulador apaga também o
  contexto guardado.
- Se o modelo não ajudar, o **separador Registos** mostra o motivo
  (`Modelo não ajudou: Ollama inacessível…`, `JSON inválido…`). Antes, uma
  falha do modelo era indistinguível de "as regras também não perceberam".
- Nada disto permite ao modelo inventar preços: se não houver cotação no
  ficheiro, a resposta continua a ser a de indisponibilidade/receção.
- No simulador aparece *"A interpretar com o modelo local…"* enquanto se espera
  — a janela não bloqueia (a chamada corre fora do thread da interface).

## 4. Testar no WhatsApp Web

1. No painel direito, **Abrir WhatsApp Web (Chrome)**.
2. Leia o QR code com o telemóvel do hotel (só é preciso uma vez; a sessão fica
   no perfil `data/teste_app/chrome_profile_whatsapp`).
3. Abra no Chrome a conversa do número autorizado.
4. Escolha o modo de envio:
   - **conversa_aberta** (recomendado para os primeiros testes) — escreve na
     conversa que está aberta.
   - **por_nome** — pesquisa o contacto indicado em "Contacto de destino".
5. Marque **"Enviar automaticamente cada resposta"** e use o simulador: cada
   resposta gerada é enviada para o WhatsApp.

**Antes de testar com clientes reais**, confirme que o modo está em `TESTE` e
que a lista de números autorizados contém apenas os números de teste.

### Ler mensagens recebidas

A opção "Ler mensagens recebidas (experimental)" percorre as conversas com
mensagens não lidas, extrai o texto da última mensagem recebida e responde.
Esta parte é a mais frágil do sistema (não existe API oficial) e deve ser
calibrada no PC real — os seletores estão todos em
`rececionista/transport/whatsapp_web.py`, no dicionário `SELECTORS`.

## 5. O que já está implementado

| Área | Estado |
|---|---|
| Motor de conversa determinístico (gates de modo/horário/números) | ✅ |
| Interpretação PT/EN/ES/FR por regras (datas, hóspedes, regime, villa) | ✅ |
| Preços por noite + total lidos de ficheiro CSV local (interruptor) | ✅ |
| Interruptor desligado: regista o pedido e remete para a receção, sem valores | ✅ |
| Alternativas ±7 dias, máximo 3, mesmo nº de hóspedes | ✅ |
| Nunca inventa: ficheiro em falta, ilegível ou sem cobertura → receção | ✅ |
| Recolha de pedido de reserva em passos + estado `pending`/`incomplete` | ✅ |
| Resposta nunca confirma a reserva | ✅ |
| Templates multilingues (introdução como assistente virtual incluída) | ✅ |
| SQLite: conversas, pedidos, eventos + purga aos 7 dias | ✅ |
| Exportação CSV dos pedidos | ✅ |
| Envio por WhatsApp Web (texto e imagens) | ✅ (calibrar seletores) |
| Interface: modos, emergência, números autorizados, histórico, logs | ✅ |
| Modelo local (Ollama) opcional para mensagens ambíguas, com fallback | ✅ |
| Contexto da conversa ("e só duas noites?") reaproveita o último orçamento | ✅ |

## 6. Ainda não implementado (fases seguintes)

- Integração real com o Newhotel (`services/pms_service.py` já existe, mas não
  é usada por esta app; a app lê o CSV, nunca o browser).
- Exportação automática do CSV a partir do Newhotel (hoje é a receção que o
  mantém).
- Debounce de 8 s nas mensagens recebidas do WhatsApp (hoje é imediato).
- Exportação PDF dos pedidos (hoje: CSV).
- Arranque automático com o Windows e PIN de acesso à aplicação.

## 7. Testes automáticos

```bat
py -3 -m unittest tests.test_rececionista_engine -v
```

57 testes cobrem: datas em 4 idiomas, hóspedes/regime/villa, gates de modo,
orçamento com preço por noite, alternativas, falha do PMS sem invenção,
fluxo de reserva completo e parcial, fecho de turno, formatação de preços,
retenção, as duas vias do interruptor de disponibilidade, a reaproveitação do
contexto da conversa ("2" depois de "quantas noites?", "só duas noites",
respostas vagas, contexto retomado após reiniciar, expiração às 24 h) e o
cliente do modelo local (com um Ollama falso: JSON com
markdown, lixo, valores fora do domínio, datas no passado, serviço em falta).
Nenhum teste precisa de Ollama, Playwright, Internet ou Newhotel.

## 8. Estrutura

```
rececionista/
├── engine.py          # orquestrador (regras de negócio)
├── nlu.py             # datas, hóspedes, regime, villa, idioma (regras)
├── fake_pms.py        # PMS fictício (usado nos testes)
├── availability.py    # preços/disponibilidade do CSV local (FilePMS)
├── llm.py             # cliente Ollama (interpretação em JSON, stdlib)
├── templates.py       # textos PT/EN/ES/FR (factos sempre por template)
├── store.py           # SQLite (mensagens, pedidos, eventos, retenção)
├── models.py          # dataclasses
├── settings.py        # configuração persistente
├── gui.py             # interface Tkinter
└── transport/
    ├── simulator.py   # canal nulo
    └── whatsapp_web.py# WhatsApp Web via Playwright
```

## 9. Dados de teste

- Documentos: `data/teste_app/docs/*.txt` (spa, restaurante, políticas).
- Fotografias: `data/teste_app/photos/T0|T1|T2/` — substitua os PNGs
  provisórios pelas fotografias reais.
- Base de dados: `data/teste_app/teste.db`.
