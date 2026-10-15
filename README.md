# Pod Rush

Catálogo Flask com painel administrativo, imagens e WhatsApp. O catálogo começa vazio.

## Rodar localmente

Requer Python 3.10 ou superior. O modo local continua usando SQLite, e o início permanece simples:

```bash
python app.py
```

Abra `http://127.0.0.1:5000`. O banco local é criado em `instance/loja.db`. O acesso inicial ao painel é `/admin`, com senha `admin`; altere a senha antes de compartilhar a máquina. Em **Configurações**, cadastre o número do WhatsApp com código do país e DDD.

Para usar outro banco local, defina `SHOP_DB`. Para produção, o app usa PostgreSQL quando `DATABASE_URL` está configurado.

## Subir para o Git

O repositório inclui o código e os arquivos de configuração. O `.gitignore` exclui o banco SQLite local, `.env`, ambientes virtuais e imagens enviadas pelo painel. Não inclua senhas ou URLs de banco no Git. O catálogo no PostgreSQL do Render começa vazio; os dados do SQLite local não são enviados automaticamente.

```bash
git init
git add .
git commit -m "Prepare Pod Rush for Render"
git branch -M main
git remote add origin URL_DO_REPOSITORIO
git push -u origin main
```

## Publicar no Render

O arquivo `render.yaml` configura um serviço Flask com Gunicorn e um PostgreSQL gerenciado. No Render, crie um **Blueprint** conectado ao repositório e confirme a criação dos recursos. Quando solicitado, informe `SHOP_ADMIN_PASSWORD` com pelo menos 14 caracteres. O Blueprint gera `SECRET_KEY` e conecta `DATABASE_URL` ao banco.

O Postgres gratuito é adequado para experimentar, mas expira após 30 dias e não oferece backups. Antes de manter a loja ativa, atualize o plano do banco no Render e faça cópias de segurança. [Limitações dos serviços gratuitos no Render](https://render.com/docs/free).

As fotos novas são armazenadas na tabela `uploaded_images` do PostgreSQL, não no disco temporário do serviço web. O schema é criado automaticamente na inicialização. Fotos antigas existentes apenas em `static/uploads/` permanecem disponíveis no SQLite local; elas não são copiadas para a instância nova do Render.

O Render publica cada alteração enviada à branch conectada. Para conferir os comandos e Blueprint atuais, veja a [documentação oficial de Flask no Render](https://render.com/docs/deploy-flask) e a [referência de `render.yaml`](https://render.com/docs/blueprint-spec).
