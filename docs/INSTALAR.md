# Instalar o Speech to Text no Mac

Sem Terminal e sem ferramentas de desenvolvedor. Você precisa de um Mac com chip Apple (M1 ou mais novo) e macOS 14 ou mais recente.

## 1. Baixar

Baixe o arquivo **`SpeechToText.dmg`** na página **Releases** do repositório.

Para pegar a versão mais recente antes de sair uma Release:

1. Abra a aba **Actions**.
2. Clique na execução mais recente com ✅ verde.
3. Em *Artifacts*, baixe **SpeechToText-dmg** e descompacte o arquivo.

## 2. Instalar

1. Abra o `SpeechToText.dmg`.
2. Arraste o **Speech to Text** para a pasta **Aplicativos** (Applications).
3. Abra o app pela pasta Aplicativos.

**Na primeira vez**, o macOS avisa que não consegue verificar o app. Isso acontece porque ele não é assinado com uma conta paga de desenvolvedor da Apple.

1. Clique em **OK / Concluído**.
2. Vá em **Ajustes do Sistema → Privacidade e Segurança**, role até o fim e clique em **Abrir Mesmo Assim** ao lado de "Speech to Text".
3. Confirme.

Você só faz isso uma vez.

## 3. Dar as permissões

O macOS vai pedir três permissões. Autorize as três:

| Permissão | Para quê |
|---|---|
| **Microfone** | ouvir você |
| **Acessibilidade** | colar o texto onde está o cursor (⌘V) |
| **Monitoramento de Entrada** | perceber quando você aperta a tecla de atalho |

Se algum pedido não aparecer, ative o Speech to Text manualmente em **Ajustes do Sistema → Privacidade e Segurança**, em cada uma dessas seções.

Depois clique no ícone 🎙 na barra de menus e escolha **Restart**.

## 4. Primeira execução

Na primeira vez, o app baixa o modelo de reconhecimento de voz (cerca de 1,6 GB). Enquanto isso, o ícone mostra ⏳. Quando virar 🎙, está pronto.

## 5. (Recomendado) Limpeza inteligente do texto

Para o app tirar os "é…", "tipo", "né", aplicar correções como "não, pera, na quinta" e formatar termos de código:

1. Instale o **Ollama**, um app gratuito: https://ollama.com/download
2. Abra o Ollama uma vez.

O Speech to Text percebe sozinho e baixa o modelo de limpeza (cerca de 2 GB, só uma vez). O andamento aparece no menu 🎙. Sem o Ollama, o app funciona do mesmo jeito, só que cola o texto sem essa limpeza.

## 6. Abrir junto com o Mac

No menu 🎙, marque **Open at login**.

## Como usar

- **Toque uma vez no Option direito (⌥)** e fale. **Toque de novo** para parar: o texto aparece onde está o cursor.
- **Esc** durante a gravação descarta o áudio.
- Fala em **português e inglês**, e mistura os dois. O idioma é detectado automaticamente.
- **Tudo o que você dita fica salvo**, mesmo quando a colagem falha. Para recuperar, use no menu 🎙:
  - **Recent**: as últimas 10 falas. Clique em uma para copiá-la.
  - **Show all history…**: o histórico completo, com busca.
- **Fix last transcription…**: corrija o que saiu errado. O app aprende com a correção.
- **Hotkey**: troque a tecla de atalho, por exemplo para a tecla 🌐 (Fn).

## Atualizar

Baixe o `.dmg` novo e substitua o app na pasta Aplicativos. O macOS pode pedir as permissões de novo depois de atualizar.
