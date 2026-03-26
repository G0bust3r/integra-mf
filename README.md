# Integra MF

Aplicativo local para transformar prints de extratos, PDFs e exportacoes bancarias em CSV pronto para importar no app Minhas Financas.

## O que esta versao faz

- Faz upload de arquivos `.csv`, `.txt`, `.xlsx`, `.ofx`, `.pdf`, `.png`, `.jpg`, `.jpeg` e `.heic`.
- Usa OCR nativo do macOS via `Vision` e `PDFKit` para ler prints e PDFs.
- Tenta reconhecer automaticamente descricao, valor e data.
- Permite revisar, editar e completar conta, cartao, categoria, subcategoria, observacoes e data de lancamento.
- Exporta no formato esperado pelo Minhas Financas, sem cabecalho.

## Como rodar

```bash
python3 app.py
```

Depois abra `http://127.0.0.1:8765`.

## Formato exportado

Cada linha sai assim:

```text
Descricao,Valor,Data Venc,Categoria,Subcategoria,Conta,Cartao,Observacoes[,Data Lancamento]
```

Observacoes:

- O arquivo final e exportado sem cabecalho.
- Valores usam ponto como separador decimal.
- Datas sao normalizadas para `DD/MM/AAAA`.
- Se existir `Data Lancamento`, ela e adicionada como nona coluna.

## Observacoes importantes

- Como os bancos usam layouts diferentes, esta primeira versao prioriza revisao humana antes da exportacao final.
- Para faturas de cartao, voce pode preencher o vencimento do lote e o nome do cartao antes de exportar.
- O arquivo `/Users/lmv/Downloads/exemplo.xlsx` serviu como referencia do layout esperado.
- Se o OCR falhar com mensagem sobre `Swift` ou `Xcode Command Line Tools`, alinhe/atualize a toolchain do macOS; o app continua funcionando para CSV, OFX e XLSX.
