"""
tests/test_rececionista_engine.py — Testes do harness de teste (sem browser).

Executar na raiz do projeto:
    python -m unittest tests.test_rececionista_engine -v

Não requer Playwright, Ollama, Newhotel nem ligação à Internet.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rececionista import nlu, settings, templates
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
