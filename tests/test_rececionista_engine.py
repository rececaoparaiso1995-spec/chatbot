"""
tests/test_rececionista_engine.py — Testes do harness de teste (sem browser).

Executar na raiz do projeto:
    python -m unittest tests.test_rececionista_engine -v

Não requer Playwright, Ollama, Newhotel nem ligação à Internet.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rececionista import llm, nlu, settings, templates
from rececionista.engine import Engine, in_window, normalize_phone, same_number
from rececionista.fake_pms import BLACKOUTS, FictitiousPMS, VILLAS
from rececionista.store import Store


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "teste.db")
        self.cfg = settings.AppConfig()
        self.cfg.mode = settings.MODE_TESTE
        self.cfg.test_numbers = ["+351900000001"]
        self.cfg.send_acknowledgement = True
        self.pms = FictitiousPMS()
        self.engine = Engine(self.cfg, pms=self.pms, store=self.store)
        self.phone = "+351900000001"

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    # Helpers -----------------------------------------------------------

    def find_available(self, nights: int = 2, adults: int = 2, meal: str = "BB") -> date:
        start = date.today() + timedelta(days=20)
        for offset in range(180):
            candidate = start + timedelta(days=offset)
            if self.pms.quote(candidate, nights, adults, 0, meal):
                return candidate
        self.fail("O PMS fictício não tem disponibilidade em 180 dias (inesperado).")

    def reply_text(self, text: str) -> str:
        return "\n".join(o.text for o in self.engine.handle(self.phone, text))


class TestNLU(unittest.TestCase):
    def test_datas_pt_numerico(self) -> None:
        today = date(2026, 10, 31)
        checkin, checkout = nlu.extract_dates(
            "queria de 12/12/2026 a 15/12/2026 para 2 pessoas", today
        )
        self.assertEqual(checkin, date(2026, 12, 12))
        self.assertEqual(checkout, date(2026, 12, 15))

    def test_datas_pt_textual(self) -> None:
        today = date(2026, 10, 31)
        checkin, checkout = nlu.extract_dates("de 12 de dezembro a 15 de dezembro", today)
        self.assertEqual(checkin, date(2026, 12, 12))
        self.assertEqual(checkout, date(2026, 12, 15))

    def test_datas_en_e_es_e_fr(self) -> None:
        today = date(2026, 10, 31)
        self.assertEqual(
            nlu.extract_dates("from 12 December to 15 December", today)[0],
            date(2026, 12, 12),
        )
        self.assertEqual(
            nlu.extract_dates("del 12 de diciembre al 15 de diciembre", today)[0],
            date(2026, 12, 12),
        )
        self.assertEqual(
            nlu.extract_dates("du 12 décembre au 15 décembre", today)[0],
            date(2026, 12, 12),
        )

    def test_data_relativa(self) -> None:
        today = date(2026, 12, 1)
        self.assertEqual(nlu.extract_dates("amanhã", today)[0], date(2026, 12, 2))
        self.assertEqual(nlu.extract_dates("tomorrow", today)[0], date(2026, 12, 2))

    def test_hospedes_regime_villa(self) -> None:
        parsed = nlu.parse(
            "queria 2 adultos e 1 criança na villa T2 com pequeno-almoço", date(2026, 10, 31)
        )
        self.assertEqual(parsed.adults, 2)
        self.assertEqual(parsed.children, 1)
        self.assertEqual(parsed.villa, "T2")
        self.assertEqual(parsed.meal, "BB")

        parsed = nlu.parse("só alojamento, somos 4", date(2026, 10, 31))
        self.assertEqual(parsed.adults, 4)
        self.assertEqual(parsed.meal, "RO")

    def test_idiomas(self) -> None:
        self.assertEqual(nlu.detect_language("Bom dia, queria saber a disponibilidade"), "pt")
        self.assertEqual(nlu.detect_language("Hello, do you have availability for tonight?"), "en")
        self.assertEqual(nlu.detect_language("Hola, ¿tienen disponibilidad para esta noche?"), "es")
        self.assertEqual(nlu.detect_language("Bonjour, avez-vous de la disponibilité ?"), "fr")

    def test_extrair_nome_nao_apanha_email(self) -> None:
        parsed = nlu.parse(
            "o meu nome é Ana Silva e o meu email é ana.silva@example.com", date(2026, 10, 31)
        )
        self.assertEqual(parsed.name, "Ana Silva")
        self.assertEqual(parsed.email, "ana.silva@example.com")

    def test_intencao_reserva(self) -> None:
        parsed = nlu.parse("Quero reservar a villa T1", date(2026, 10, 31))
        self.assertEqual(parsed.intent, "booking_confirm")

    def test_frase_curta_nao_e_detectada_como_ingles(self) -> None:
        # Regressão: "horário" contém a letra "i" e era pontuado como inglês.
        self.assertIsNone(nlu.detect_language("Qual é o horário do spa?"))
        self.assertEqual(nlu.detect_language("Obrigado!"), "pt")


class TestGates(Base):
    def test_desligado_nao_responde(self) -> None:
        self.cfg.mode = settings.MODE_DESLIGADO
        self.assertEqual(self.engine.handle(self.phone, "Bom dia"), [])

    def test_emergencia_nao_responde(self) -> None:
        self.cfg.mode = settings.MODE_EMERGENCIA
        self.assertEqual(self.engine.handle(self.phone, "Bom dia"), [])

    def test_modo_teste_ignora_nao_autorizado(self) -> None:
        out = self.engine.handle("+351999999999", "Bom dia")
        self.assertEqual(out, [])
        rows = self.store.conversation("+351999999999")
        self.assertEqual(len(rows), 1)  # registada, mas não respondida

    def test_horario_atravessa_meia_noite(self) -> None:
        self.assertTrue(in_window(datetime(2026, 11, 1, 23, 0), "22:30", "07:00"))
        self.assertTrue(in_window(datetime(2026, 11, 1, 6, 30), "22:30", "07:00"))
        self.assertFalse(in_window(datetime(2026, 11, 1, 12, 0), "22:30", "07:00"))

    def test_telefone_normalizado(self) -> None:
        self.assertEqual(normalize_phone("+351 912 345 678"), "351912345678")
        self.assertEqual(normalize_phone("00351 912 345 678"), "351912345678")

    def test_mesmo_numero_em_formatos_diferentes(self) -> None:
        for escrito in ("+351 912 345 678", "351912345678", "912345678", "912 345 678"):
            lead = f"Formatos: {escrito!r}"
            self.assertTrue(same_number(escrito, "+351912345678"), lead)
            self.assertTrue(same_number("+351912345678", escrito), lead)

    def test_numeros_diferentes_nao_coincidem(self) -> None:
        self.assertFalse(same_number("912345678", "351912345679"))
        self.assertFalse(same_number("912345678", "351999999999"))
        self.assertFalse(same_number("", "351912345678"))

    def test_modo_teste_aceita_numero_sem_indicativo(self) -> None:
        self.cfg.test_numbers = ["+351 912 345 678"]
        outs = self.engine.handle("912345678", "Boa noite, têm disponibilidade?")
        self.assertTrue(outs, "o número nacional devia ser reconhecido como autorizado")


class TestConsulta(Base):
    def test_mantem_idioma_da_sessao_quando_a_mensagem_e_ambigua(self) -> None:
        self.engine.handle(self.phone, "Boa noite, tenho uma dúvida")  # sessão em PT
        outs = self.engine.handle(self.phone, "Qual é o horário do spa?")
        self.assertTrue(any("Sobre " in o.text for o in outs), [o.text for o in outs])

    def test_orcamento_com_preco_por_noite_e_total(self) -> None:
        checkin = self.find_available(nights=3)
        text = (
            f"Boa noite, queria disponibilidade de {checkin:%d/%m/%Y} a "
            f"{(checkin + timedelta(days=3)):%d/%m/%Y} para 2 pessoas com pequeno-almoço"
        )
        reply = self.reply_text(text)
        self.assertIn("IVA incluído", reply)
        self.assertIn("Total da estadia", reply)
        self.assertIn(templates.dmy(checkin), reply)
        self.assertIn(templates.dmy(checkin + timedelta(days=2)), reply)
        self.assertIn("€", reply)

    def test_sem_disponibilidade_sugere_alternativas(self) -> None:
        # Período deliberadamente esgotado para T0
        reply = self.reply_text(
            "Bom dia, têm disponibilidade de 28/12/2026 a 31/12/2026 "
            "para 2 pessoas na villa T0?"
        )
        self.assertIn("não tenho disponibilidade", reply)
        self.assertIn("alternativas", reply)
        # Não pode inventar: só aparece o que vem do PMS fictício
        self.assertNotIn("Peço desculpa", reply)

    def test_falha_do_pms_nao_inventa(self) -> None:
        class EmptyPMS(FictitiousPMS):
            def quote(self, *args, **kwargs):
                return []

            def alternatives(self, *args, **kwargs):
                return []

        self.engine.pms = EmptyPMS()
        reply = self.reply_text(
            "Bom dia, queria disponibilidade de 12/12/2026 a 14/12/2026 para 2 pessoas"
        )
        self.assertIn("encaminhar", reply.lower())
        self.assertNotIn("€", reply)

    def test_ack_antes_da_consulta(self) -> None:
        checkin = self.find_available(nights=2)
        outs = self.engine.handle(
            self.phone,
            f"disponibilidade de {checkin:%d/%m/%Y} a {(checkin + timedelta(days=2)):%d/%m/%Y} para 2",
        )
        self.assertGreaterEqual(len(outs), 2)
        self.assertIn("verificar a disponibilidade", outs[-2].text + outs[-1].text)


class TestPedido(Base):
    def _quote_first(self) -> date:
        checkin = self.find_available(nights=2)
        self.engine.handle(
            self.phone,
            f"Boa noite, disponibilidade de {checkin:%d/%m/%Y} a "
            f"{(checkin + timedelta(days=2)):%d/%m/%Y} para 2 pessoas com pequeno-almoço",
        )
        return checkin

    def test_fluxo_completo_de_reserva(self) -> None:
        self._quote_first()
        reply = self.reply_text("Quero reservar")
        self.assertIn("nome", reply.lower())

        reply = self.reply_text("O meu nome é Ana Silva e o meu email é ana.silva@example.com")
        self.assertIn("Pedido registado", reply)
        self.assertIn("NÃO está confirmado", reply)

        bookings = self.store.bookings()
        self.assertEqual(len(bookings), 1)
        row = bookings[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["name"], "Ana Silva")
        self.assertEqual(row["email"], "ana.silva@example.com")
        self.assertGreater(row["total"], 0)

    def test_pedido_parcial_fica_incompleto(self) -> None:
        self._quote_first()
        self.engine.handle(self.phone, "Quero reservar")
        for _ in range(3):
            self.engine.handle(self.phone, "Sim, quero avançar")

        bookings = self.store.bookings()
        self.assertEqual(len(bookings), 1)
        self.assertEqual(bookings[0]["status"], "incomplete")

    def test_reserva_apenas_depois_de_orcamento(self) -> None:
        reply = self.reply_text("Quero reservar")
        self.assertIn("datas", reply.lower())
        self.assertEqual(self.store.bookings(), [])


class TestPedidoComDadosAntesDasDatas(Base):
    def test_contacto_antes_das_datas_nao_se_perde(self) -> None:
        checkin = self.find_available(nights=2)
        self.engine.handle(self.phone, "Quero reservar")
        self.engine.handle(
            self.phone, "O meu nome é Ana Silva e o meu email é ana.silva@example.com"
        )
        reply = self.reply_text(
            f"de {checkin:%d/%m/%Y} a {(checkin + timedelta(days=2)):%d/%m/%Y} "
            "para 2 pessoas com pequeno-almoço"
        )
        self.assertIn("Total da estadia", reply)   # preço apresentado primeiro
        self.assertIn("Ana Silva", reply)          # contacto preservado
        self.assertIn("NÃO está confirmado", reply)
        self.assertEqual(len(self.store.bookings()), 1)

    def test_pedido_so_com_datas_fica_incompleto(self) -> None:
        checkin = self.find_available(nights=2)
        self.engine.handle(self.phone, "Quero reservar")
        # A 1.ª mensagem cria o rascunho; só depois de 3 perguntas sem resposta
        # é que o pedido é guardado como incompleto.
        for _ in range(4):
            self.engine.handle(
                self.phone,
                f"de {checkin:%d/%m/%Y} a {(checkin + timedelta(days=2)):%d/%m/%Y} para 2",
            )
        self.assertEqual(len(self.store.bookings()), 1)
        self.assertEqual(self.store.bookings()[0]["status"], "incomplete")


class TestFechoEFormato(Base):
    def test_mensagem_de_fecho_do_turno(self) -> None:
        self.engine.handle(self.phone, "Bom dia")
        closing = self.engine.end_shift(active_minutes=120)
        self.assertEqual(len(closing), 1)
        self.assertIn("receção retoma", closing[0].text)

    def test_formato_de_precos(self) -> None:
        self.assertEqual(templates.money("pt", 1234.5), "1.234,50 €")
        self.assertEqual(templates.money("en", 1234.5), "€1,234.50")
        self.assertEqual(templates.money("fr", 999.99), "999,99 €")

    def test_traducao_de_regime(self) -> None:
        self.assertIn("breakfast", templates.meal_label("en", "BB"))
        self.assertIn("solo", templates.meal_label("es", "RO"))

    def test_pms_ficticio_e_deterministico(self) -> None:
        villa = VILLAS[0]
        day = date(2027, 3, 10)
        first = self.pms.nightly_price(villa, day, "BB", 2)
        second = self.pms.nightly_price(villa, day, "BB", 2)
        self.assertEqual(first, second)
        self.assertGreater(first, villa.base_rate)


class TestContextoPersistente(Base):
    """O contexto guardado na base de dados sobrevive a reiniciar a app."""

    def new_engine(self) -> Engine:
        """Simula um arranque novo: sessões em memória vazias, mesma base."""
        return Engine(self.cfg, pms=self.pms, store=self.store)

    def test_orcamento_sobrevive_ao_arranque(self) -> None:
        day = self.find_available(nights=3)
        self.engine.handle(
            self.phone,
            f"de {day.strftime('%d/%m/%Y')} a "
            f"{(day + timedelta(days=3)).strftime('%d/%m/%Y')} para 2 pessoas",
        )

        text = "\n".join(
            o.text for o in self.new_engine().handle(self.phone, "e se fossem só 2 noites?")
        )
        self.assertIn("€", text)
        self.assertIn(templates.dmy(day), text)

    def test_consulta_pendente_sobrevive_ao_arranque(self) -> None:
        day = self.find_available(nights=2)
        self.engine.handle(
            self.phone,
            f"disponibilidade para {day.strftime('%d/%m/%Y')} para 2 pessoas",
        )

        text = "\n".join(o.text for o in self.new_engine().handle(self.phone, "2"))
        self.assertIn("€", text)
        self.assertIn(templates.dmy(day), text)

    def test_contexto_com_mais_de_24h_e_ignorado(self) -> None:
        day = self.find_available(nights=2)
        self.engine.handle(
            self.phone,
            f"disponibilidade para {day.strftime('%d/%m/%Y')} para 2 pessoas",
        )

        guardado = self.store.load_session_state(self.phone)
        self.assertIsNotNone(guardado)
        assert guardado is not None
        payload, _ = guardado
        antigo = datetime.now() - timedelta(hours=25)
        self.store.save_session_state(self.phone, payload, at=antigo)

        text = "\n".join(o.text for o in self.new_engine().handle(self.phone, "2"))
        self.assertNotIn("€", text)
        # O contexto antigo foi descartado: a linha que fica é a nova, vazia.
        guardado = self.store.load_session_state(self.phone)
        self.assertIsNotNone(guardado)
        assert guardado is not None
        payload = json.loads(guardado[0])
        self.assertIsNone(payload["last_offer"])
        self.assertIsNone(payload["pending_quote"])

    def test_limpar_conversa_apaga_o_contexto_guardado(self) -> None:
        day = self.find_available(nights=2)
        self.engine.handle(
            self.phone,
            f"disponibilidade para {day.strftime('%d/%m/%Y')} para 2 pessoas",
        )
        self.assertIsNotNone(self.store.load_session_state(self.phone))

        self.engine.reset_sessions()
        self.assertIsNone(self.store.load_session_state(self.phone))

    def test_sem_base_de_dados_continua_a_funcionar_em_memoria(self) -> None:
        engine = Engine(self.cfg, pms=self.pms, store=None)
        day = self.find_available(nights=2)
        engine.handle(
            self.phone,
            f"de {day.strftime('%d/%m/%Y')} a "
            f"{(day + timedelta(days=2)).strftime('%d/%m/%Y')} para 2 pessoas",
        )
        text = "\n".join(
            o.text for o in engine.handle(self.phone, "e se fossem só 3 noites?")
        )
        self.assertIn("€", text)


class FakeLLM:
    """Substitui o Ollama nos testes: devolve o que lhe mandarem."""

    def __init__(self, result=None):
        self.result = result
        self.calls = 0
        self.last_history: list[tuple[str, str]] = []

    def interpret(self, message, today=None, history=None):
        self.calls += 1
        self.last_history = list(history or [])
        return self.result


class BaseComLLM(Base):
    def setUp(self) -> None:
        super().setUp()
        self.cfg.use_llm = True

    def engine_with(self, fake: FakeLLM) -> Engine:
        return Engine(self.cfg, pms=self.pms, store=self.store, llm_client=fake)


class TestCompreensaoComLLM(BaseComLLM):
    """O modelo só entra quando as regras não percebem a mensagem."""

    FRASE_AMBIGUA = "andava a ver se dava para ficar aí uns dias em dezembro"

    def test_regras_resolvem_e_o_modelo_nao_e_chamado(self) -> None:
        fake = FakeLLM(None)
        engine = self.engine_with(fake)
        day = self.find_available()
        engine.handle(
            self.phone,
            f"tem disponibilidade de {day.strftime('%d/%m/%Y')} a "
            f"{(day + timedelta(days=2)).strftime('%d/%m/%Y')} para 2 pessoas",
        )
        self.assertEqual(fake.calls, 0)

    def test_modelo_resolve_intencao_e_datas(self) -> None:
        day = self.find_available()
        fake = FakeLLM(
            llm.LlmResult(intent="availability", checkin=day, nights=2, adults=2)
        )
        engine = self.engine_with(fake)
        text = "\n".join(o.text for o in engine.handle(self.phone, self.FRASE_AMBIGUA))
        self.assertEqual(fake.calls, 1)
        self.assertIn("€", text)
        self.assertIn("noite", text.lower())

    def test_modelo_indisponivel_volta_às_regras(self) -> None:
        fake = FakeLLM(None)
        engine = self.engine_with(fake)
        text = "\n".join(o.text for o in engine.handle(self.phone, self.FRASE_AMBIGUA))
        self.assertEqual(fake.calls, 1)
        self.assertIn("não compreendi", text.lower())

    def test_desligado_no_configuracao_nao_chama_o_modelo(self) -> None:
        self.cfg.use_llm = False
        fake = FakeLLM(llm.LlmResult(intent="availability"))
        engine = self.engine_with(fake)
        engine.handle(self.phone, self.FRASE_AMBIGUA)
        self.assertEqual(fake.calls, 0)

    def test_modelo_recebe_o_historico_da_conversa(self) -> None:
        fake = FakeLLM(llm.LlmResult(intent="greeting"))
        engine = self.engine_with(fake)
        engine.handle(self.phone, "Bom dia")
        engine.handle(self.phone, self.FRASE_AMBIGUA)
        self.assertEqual(fake.calls, 1)
        self.assertTrue(any("Bom dia" in text for _, text in fake.last_history))
        # Histórico por ordem cronológica e sem a mensagem atual.
        self.assertEqual(fake.last_history[0][0], "user")
        self.assertEqual(fake.last_history[-1][0], "assistant")
        self.assertNotIn(self.FRASE_AMBIGUA, [t for _, t in fake.last_history])


class TestContextoDaConversa(Base):
    def test_duas_noites_aproveita_a_entrada_anterior(self) -> None:
        day = self.find_available(nights=3)
        first = "\n".join(
            o.text
            for o in self.engine.handle(
                self.phone,
                f"de {day.strftime('%d/%m/%Y')} a "
                f"{(day + timedelta(days=3)).strftime('%d/%m/%Y')} para 2 pessoas",
            )
        )
        self.assertIn("€", first)

        second = "\n".join(
            o.text for o in self.engine.handle(self.phone, "e se fossem só 2 noites?")
        )
        self.assertIn("€", second)
        self.assertIn(templates.dmy(day), second)
        self.assertNotIn("datas de entrada e saída", second.lower())

    def test_responder_2_a_pergunta_das_noites_completa_o_pedido(self) -> None:
        day = self.find_available(nights=2)
        first = "\n".join(
            o.text
            for o in self.engine.handle(
                self.phone,
                f"queria saber a disponibilidade para {day.strftime('%d/%m/%Y')} "
                "para 2 pessoas",
            )
        )
        self.assertIn("quantas noites", first.lower())

        second = "\n".join(o.text for o in self.engine.handle(self.phone, "2"))
        self.assertIn("€", second)
        self.assertIn(templates.dmy(day), second)

    def test_noites_por_extenso_tambem_completa_o_pedido(self) -> None:
        day = self.find_available(nights=2)
        self.engine.handle(
            self.phone,
            f"disponibilidade para {day.strftime('%d/%m/%Y')} para 2 pessoas",
        )
        text = "\n".join(o.text for o in self.engine.handle(self.phone, "2 noites"))
        self.assertIn("€", text)

    def test_respostas_vagas_de_noites(self) -> None:
        casos = {
            "uns dias": 2,
            "fim de semana": 2,
            "3-4 noites": 3,
            "duas noites": 2,
            "uma semana": 7,
            "cerca de 4": 4,
        }
        for answer, nights in casos.items():
            with self.subTest(answer=answer):
                self.engine.reset_sessions()
                day = self.find_available(nights=nights)
                first = "\n".join(
                    o.text
                    for o in self.engine.handle(
                        self.phone,
                        f"disponibilidade para {day.strftime('%d/%m/%Y')} para 2 pessoas",
                    )
                )
                self.assertIn("quantas noites", first.lower())
                second = "\n".join(
                    o.text for o in self.engine.handle(self.phone, answer)
                )
                self.assertIn(templates.dmy(day), second)
                self.assertIn(f"{nights} noite(s)", second)

    def test_numero_solto_sem_consulta_pendente_nao_e_noites(self) -> None:
        text = "\n".join(o.text for o in self.engine.handle(self.phone, "2"))
        self.assertNotIn("€", text)

    def test_sem_orcamento_anterior_pede_datas(self) -> None:
        text = "\n".join(o.text for o in self.engine.handle(self.phone, "e 2 noites?"))
        self.assertIn("datas", text.lower())


class TestClienteOllama(unittest.TestCase):
    """Validação da resposta do modelo, sem rede."""

    def client(self) -> llm.OllamaClient:
        return llm.OllamaClient("http://localhost:11434", "modelo-teste", 5)

    def test_json_com_markdown_e_aceite(self) -> None:
        result = self.client()._to_result(
            '```json\n{"intent": "availability", "nights": 2}\n```', date(2026, 11, 1)
        )
        self.assertEqual(result.intent, "availability")
        self.assertEqual(result.nights, 2)

    def test_lixo_do_modelo_da_erro_em_vez_de_inventar(self) -> None:
        with self.assertRaises(llm.LlmError):
            self.client()._to_result("Não sei responder a isso.", date(2026, 11, 1))

    def test_valores_fora_do_dominio_sao_descartados(self) -> None:
        result = self.client()._to_result(
            '{"intent": "comprar_um_carro", "villa": "T9", "meal": "XP", '
            '"adults": 99, "language": "de"}',
            date(2026, 11, 1),
        )
        self.assertEqual(result.intent, "unclear")
        self.assertIsNone(result.villa)
        self.assertIsNone(result.meal)
        self.assertIsNone(result.adults)
        self.assertIsNone(result.language)
        self.assertFalse(result.is_useful())

    def test_datas_no_passado_sao_rejeitadas(self) -> None:
        result = self.client()._to_result(
            '{"intent": "availability", "checkin": "2020-01-01", "nights": 2}',
            date(2026, 11, 1),
        )
        self.assertIsNone(result.checkin)

    def test_ollama_em_falta_nao_levanta(self) -> None:
        client = llm.OllamaClient("http://127.0.0.1:9", "modelo-teste", 1)
        self.assertIsNone(client.interpret("olá"))
        ok, message = client.check()
        self.assertFalse(ok)
        self.assertTrue(message)


class TestDisponibilidadePorFicheiro(unittest.TestCase):
    """Toggle: ficheiro local ligado, ou pedido registado para a receção."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = Store(self.dir / "teste.db")
        self.csv = self.dir / "disponibilidade.csv"
        self.cfg = settings.AppConfig()
        self.cfg.mode = settings.MODE_TESTE
        self.cfg.test_numbers = ["+351900000001"]
        self.cfg.send_acknowledgement = False
        self.phone = "+351900000001"

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def engine(self) -> Engine:
        return Engine(self.cfg, store=self.store)

    def write_csv(self, days: int = 10, price: str = "200.00") -> None:
        start = date.today() + timedelta(days=5)
        lines = ["data,villa,disponivel,preco_bb,preco_ro"]
        for i in range(days):
            day = start + timedelta(days=i)
            for code in ("T0", "T1", "T2"):
                lines.append(f"{day.isoformat()},{code},sim,{price},{price}")
        self.csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.checkin = start

    def test_desligado_regista_pedido_sem_precos(self) -> None:
        self.cfg.use_availability_file = False
        out = self.engine().handle(
            self.phone, "queria um quarto de 20 a 23 de dezembro para 2"
        )
        text = "\n".join(o.text for o in out)
        self.assertIn("20/12", text)
        self.assertIn("receção", text.lower())
        self.assertNotIn("€", text)
        self.assertTrue(any(o.meta.get("escalate") for o in out))

    def test_ligado_le_precos_do_ficheiro(self) -> None:
        self.write_csv()
        self.cfg.use_availability_file = True
        self.cfg.availability_file = str(self.csv)
        day = self.checkin
        text = "\n".join(
            o.text
            for o in self.engine().handle(
                self.phone,
                f"tem disponibilidade de {day.strftime('%d/%m/%Y')} a "
                f"{(day + timedelta(days=2)).strftime('%d/%m/%Y')} para 2 pessoas",
            )
        )
        self.assertIn("200,00", text)
        self.assertNotIn("receção", text.lower().split("total")[0])

    def test_ligado_sem_cobertura_nao_inventa(self) -> None:
        self.write_csv(days=3)
        self.cfg.use_availability_file = True
        self.cfg.availability_file = str(self.csv)
        far = date.today() + timedelta(days=200)
        text = "\n".join(
            o.text
            for o in self.engine().handle(
                self.phone,
                f"de {far.strftime('%d/%m/%Y')} a "
                f"{(far + timedelta(days=2)).strftime('%d/%m/%Y')} para 2",
            )
        )
        self.assertIn("receção", text.lower())
        self.assertNotIn("€", text)

    def test_ficheiro_em_falta_tambem_vai_para_a_rececao(self) -> None:
        self.cfg.use_availability_file = True
        self.cfg.availability_file = str(self.dir / "nao_existe.csv")
        out = self.engine().handle(
            self.phone, "queria de 20 a 23 de dezembro para 2 pessoas"
        )
        text = "\n".join(o.text for o in out)
        self.assertIn("receção", text.lower())
        self.assertTrue(any(o.meta.get("escalate") for o in out))


class TestRetencao(Base):
    def test_purga_apaga_conversas_antigas(self) -> None:
        old = datetime.now() - timedelta(days=10)
        self.store.save_message(self.phone, "in", "antiga", created_at=old)
        self.store.save_message(self.phone, "in", "recente")
        removed = self.store.purge(days=7)
        self.assertGreaterEqual(removed, 1)
        bodies = [row["body"] for row in self.store.conversation(self.phone)]
        self.assertIn("recente", bodies)
        self.assertNotIn("antiga", bodies)


if __name__ == "__main__":
    unittest.main(verbosity=2)
