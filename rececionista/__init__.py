"""
rececionista — Harness de teste do Rececionista Virtual (Paraíso Villas & Spa)

Versão de TESTE (Fase T):
  - NÃO contacta o Newhotel e NÃO usa navegador para consultar disponibilidade.
    Preços e disponibilidade vêm de um CSV local (`availability.FilePMS`),
    quando esse modo está ligado na configuração; caso contrário o pedido é
    registado para a receção responder.
  - Permite validar o fluxo conversacional, os templates multilingues e o
    envio real via WhatsApp Web (Playwright), sem tocar em dados do hotel.

`fake_pms.FictitiousPMS` continua a existir para os testes automáticos.
Este pacote é intencionalmente independente de `services/pms_service.py`
(módulo real do PMS), que continua a ser testado pela sua própria suite.
"""

__version__ = "0.1.0-teste"
