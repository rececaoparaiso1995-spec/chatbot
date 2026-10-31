# App de teste — Rececionista Virtual (Paraíso Villas & Spa)

Aplicação **independente** para validar o fluxo conversacional e o envio por
**WhatsApp Web**, **sem qualquer ligação ao Newhotel**.

> Todos os preços e disponibilidades são **fictícios e determinísticos**
> (`rececionista/fake_pms.py`). O mesmo dia e a mesma villa produzem sempre o
> mesmo preço, para que os testes sejam comparáveis.

---

## 1. Requisitos

- Windows 10/11 com Python 3.11 ou superior (python.org, com "Add to PATH").
- Para envio real: Google Chrome instalado.

```bat
pip install playwright
playwright install chromium
```

Nada mais é necessário: a interface usa Tkinter (incluído no Python).

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
2. Use os botões de exemplo (Disponibilidade PT/EN/ES/FR, Sem disponibilidade,
   Fotos, Spa, Quero reservar, Áudio) ou escreva livremente.
3. As respostas aparecem no painel; os pedidos de reserva vão para o separador
   **Pedidos de reserva** com estado `pending`.

Fluxo completo a experimentar:

```
Disponibilidade 12/12/2026 a 15/12/2026 para 2 pessoas com pequeno-almoço
→ (preço por noite + total, com aviso "estou a verificar")

Quero reservar
→ pede nome e email

O meu nome é Ana Silva e o meu email é ana.silva@example.com
→ resumo do pedido + "NÃO está confirmado"
```

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
| Preços fictícios por noite + total, com variação por época/fim de semana | ✅ |
| Alternativas ±7 dias, máximo 3, mesmo nº de hóspedes | ✅ |
| Nunca inventa: falha do PMS → escalonamento para a receção | ✅ |
| Recolha de pedido de reserva em passos + estado `pending`/`incomplete` | ✅ |
| Resposta nunca confirma a reserva | ✅ |
| Templates multilingues (introdução como assistente virtual incluída) | ✅ |
| SQLite: conversas, pedidos, eventos + purga aos 7 dias | ✅ |
| Exportação CSV dos pedidos | ✅ |
| Envio por WhatsApp Web (texto e imagens) | ✅ (calibrar seletores) |
| Interface: modos, emergência, números autorizados, histórico, logs | ✅ |

## 6. Ainda não implementado (fases seguintes)

- Integração real com o Newhotel (`services/pms_service.py` já existe, mas não
  é usada por esta app).
- Debounce de 8 s nas mensagens recebidas do WhatsApp (hoje é imediato).
- Escolha/uso do modelo Ollama (esta build é 100 % por regras e templates).
- Exportação PDF dos pedidos (hoje: CSV).
- Arranque automático com o Windows e PIN de acesso à aplicação.

## 7. Testes automáticos

```bat
py -3 -m unittest tests.test_rececionista_engine -v
```

29 testes cobrem: datas em 4 idiomas, hóspedes/regime/villa, gates de modo,
orçamento com preço por noite, alternativas, falha do PMS sem invenção,
fluxo de reserva completo e parcial, fecho de turno, formatação de preços e
retenção.

## 8. Estrutura

```
rececionista/
├── engine.py          # orquestrador (regras de negócio)
├── nlu.py             # datas, hóspedes, regime, villa, idioma (regras)
├── fake_pms.py        # PMS fictício determinístico
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
