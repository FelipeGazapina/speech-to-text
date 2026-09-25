# Instalar o Speech to Text no Mac

Sem Terminal e sem ferramentas de desenvolvedor. Você precisa de um Mac com chip Apple (M1 ou mais novo) e macOS 14 ou mais recente.

## 1. Baixar

Baixe o arquivo **`SpeechToText.dmg`** da [última release](https://github.com/FelipeGazapina/speech-to-text/releases/latest).

## 2. Instalar

1. Abra o `SpeechToText.dmg`.
2. Arraste o **Speech to Text** para a pasta **Aplicativos** (Applications).
3. Abra o app pela pasta Aplicativos.

**Na primeira vez**, o macOS mostra a janela **"Speech to Text" Not Opened** ("não foi aberto"), dizendo que a Apple não conseguiu verificar o app. Isso acontece porque o app não é assinado com uma conta paga de desenvolvedor da Apple. **Não é um travamento** e o app não está com defeito.

1. Clique em **Done** (Concluído). **Não** clique em *Move to Trash*.
2. Abra **Ajustes do Sistema → Privacidade e Segurança** (System Settings → Privacy & Security).
3. Role até a seção **Segurança** (Security). Vai aparecer *"Speech to Text" was blocked to protect your Mac*. Clique em **Abrir Mesmo Assim** (Open Anyway).
4. Digite sua senha ou use o Touch ID, e confirme em **Abrir Mesmo Assim** mais uma vez.

Você só faz isso uma vez por versão. O botão **Abrir Mesmo Assim** só aparece durante cerca de uma hora depois da tentativa bloqueada. Se ele não estiver lá, tente abrir o app de novo e volte aos Ajustes.

**Alternativa em uma linha:** se o botão não aparecer, abra o app **Terminal**, cole o comando abaixo e aperte Enter. Depois abra o app normalmente.

```
xattr -dr com.apple.quarantine "/Applications/Speech to Text.app"
```

Esse comando só remove a marca de "baixado da internet" que o macOS põe no app.

## 3. Dar as permissões

O macOS vai pedir três permissões. Autorize as três:

| Permissão | Para quê |
|---|---|
| **Microfone** | ouvir você |
| **Acessibilidade** | colar o texto onde está o cursor (⌘V) |
| **Monitoramento de Entrada** | perceber quando você aperta a tecla de atalho |
| **Gravação de Tela e Áudio do Sistema** | só para as notas de reunião: ouvir as outras pessoas da chamada (pedida na primeira reunião) |

Se algum pedido não aparecer, ative o Speech to Text manualmente em **Ajustes do Sistema → Privacidade e Segurança**, em cada uma dessas seções.

Depois clique no ícone 🎙 na barra de menus e escolha **Restart**.

## 4. Primeira execução

Na primeira vez, o app baixa o modelo de reconhecimento de voz (cerca de 1,6 GB). Enquanto isso, o ícone mostra ⏳ com a porcentagem (por exemplo ⏳ 45%). **O ditado só funciona quando o ícone virar 🎙.** Se você apertar o atalho antes, o Mac faz um som de erro e nada é gravado.

Se o app for fechado ou reiniciado no meio do download, ele continua de onde parou.

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

## Notas de reunião (Notetaker)

No menu 🎙, clique em **📝 Start meeting notes** quando a reunião começar e em **⏹ Stop meeting notes** quando terminar. O app grava duas coisas ao mesmo tempo:

- **o seu microfone**, que aparece como "Você";
- **o áudio do computador**, ou seja, as outras pessoas no Zoom, Meet, Teams, Slack etc., que aparece como "Outros".

Ao parar, ele transcreve tudo no seu Mac, com horário e quem falou, e faz um resumo (com título, pontos principais, decisões e próximos passos) usando o Ollama. O ditado continua funcionando durante a reunião.

Para ver as notas, abra **Open Speech to Text…** no menu 🎙:
- **Notetaker:** a lista de reuniões, cada uma com o **Resumo** e a **Transcrição** original.
- **Dictations:** o histórico de ditados, com busca.

**Armazenamento:** o texto fica salvo no banco local (SQLite). O áudio **não** é guardado: ele é apagado assim que a transcrição termina.

**Permissão para ouvir as outras pessoas:** na primeira reunião, o macOS pede **Gravação de Tela e Áudio do Sistema** (Screen & System Audio Recording). Autorize e reinicie o app. A tela não é gravada, só o som. Sem essa permissão, a reunião grava apenas o seu microfone, e a nota avisa isso.

## Atualizar

Baixe o `.dmg` novo e substitua o app na pasta Aplicativos. O macOS pode pedir as permissões de novo depois de atualizar.

## Se algo não funcionar

**O atalho não faz nada.** Quase sempre falta a permissão de **Monitoramento de Entrada**. O menu 🎙 mostra ⚠️ e um botão **Allow Input Monitoring…**: clique nele, ative o **Speech to Text** na lista, e o app reinicia sozinho já funcionando.

Se o Speech to Text já aparece ativado e mesmo assim não funciona (comum depois de atualizar o app):
1. Selecione o Speech to Text na lista.
2. Remova-o com o botão **–**.
3. Adicione de novo com **+**, escolhendo o app na pasta Aplicativos.

Faça o mesmo em **Acessibilidade**.

**Gravou, mas não colou nada.** Confira três coisas no menu 🎙:
- O ícone ainda mostra ⏳? Então o modelo não terminou de baixar. Espere virar 🎙.
- Aparece ⚠️ **Allow Accessibility…**? Sem essa permissão o app não consegue colar. O texto fica na área de transferência: é só apertar ⌘V.
- Tudo o que você ditou fica em **Recent** e em **Show all history…**.

**O app travou.** Ele se protege:
- Ligar o microfone tem prazo, e desligar nunca fica esperando o sistema de áudio.
- Uma transcrição que não termina a tempo fica salva em **Recent** (❌, clique para tentar de novo), e o modelo é reiniciado.
- Se mesmo assim o app ficar 45 segundos sem responder, ele salva a gravação em andamento, registra no log onde travou e reinicia sozinho, avisando o que aconteceu.

**O app fechou sozinho ou travou.** Me mande os arquivos de log: menu 🎙 → **Advanced → Show log files in Finder**. Envie os arquivos `speech-to-text.log` e `speech-to-text-console.log`.

Se o app nem chega a abrir:
1. Abra o app **Console** (pelo Spotlight).
2. Clique em **Relatórios de Falhas** na barra lateral.
3. Procure "Speech to Text" e copie o relatório.
