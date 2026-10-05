# Fase 2 · Menú semanal (requisitos acordados)

- 4 comidas al día: desayuno, comida (táper en el trabajo de lunes a miércoles, jornada L-J 8:00-17:30; el jueves pasta en la empresa), merienda/post-entreno y cena.
- Se cocina cada noche la cena y el táper del día siguiente (sin batch cooking).
- El tiempo de cocina lo decide el bot según la carga de entreno del día siguiente o del propio día: recetas rápidas los días duros o con doble sesión y más elaboradas los días suaves.
- Desayuno y merienda casi fijos. Comida y cena varían a lo largo de la semana.
- El menú semanal completo se envía junto con la lista de la compra del sábado (no hay aviso nocturno). Debe usar solo alimentos de `alimentos.py` y partir de las cantidades de `menu.menu_dia`.

**Hecho:** `menu.py` y `/menu` ya dan un plato por comida con sus gramos (con whey y creatina), según los alimentos que come el atleta, y la lista de la compra suma esos menús. El menú semanal se envía el sábado en un mensaje aparte de la lista (`menu.formatear_semana`, también con `/menu semana`), y `/plato` o el coach permiten cambiar platos. Falta elegir recetas según el tiempo de cocina y la carga de entreno.
