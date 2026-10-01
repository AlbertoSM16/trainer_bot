# Fase 2 · Menú semanal (requisitos acordados, pendiente de implementar)

- 4 comidas al día: desayuno, comida (táper en el trabajo, jornada 8-18), merienda/post-entreno y cena.
- Se cocina cada noche la cena y el táper del día siguiente (sin batch cooking).
- El tiempo de cocina lo decide el bot según la carga de entreno del día siguiente o del propio día: recetas rápidas los días duros o con doble sesión y más elaboradas los días suaves.
- Desayuno y merienda casi fijos. Comida y cena varían a lo largo de la semana.
- El menú semanal completo se envía junto con la lista de la compra del sábado (no hay aviso nocturno). Debe usar solo alimentos de `alimentos.py` y partir de las cantidades de `menu.menu_dia`.

**Hecho:** `menu.py` y `/menu` ya reparten por comidas los gramos de cada alimento (con whey y creatina) y la lista de la compra suma esos menús. Falta convertirlo en platos o recetas con tiempo de cocina y enviar el menú semanal del sábado.
- Telegram limita los mensajes a 4096 caracteres, así que probablemente haga falta enviar el menú en un mensaje aparte de la lista.
