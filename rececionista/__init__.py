"""
rececionista — Harness de teste do Rececionista Virtual (Paraíso Villas & Spa)

Versão de TESTE (Fase T):
  - NÃO contacta o Newhotel. Todos os preços e disponibilidades são FICTÍCIOS
    e determinísticos, gerados por `fake_pms.FictitiousPMS`.
  - Permite validar o fluxo conversacional, os templates multilingues e o
    envio real via WhatsApp Web (Playwright), sem tocar em dados do hotel.

Este pacote é intencionalmente independente de `services/pms_service.py`
(módulo real do PMS), que continua a ser testado pela sua própria suite.
"""

__version__ = "0.1.0-teste"
