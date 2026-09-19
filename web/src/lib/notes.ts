import type { Screen } from "./store";

/** Talking points for the presenter drawer (toggle with N). Qualitative on purpose: the numbers are on screen. */
export const NOTES: Record<"en" | "es", Record<Screen, string[]>> = {
  en: {
    national: [
      "Start national: Sweden produces in the north (hydro, wind) and consumes in the south; the price rises from SE1 to SE4.",
      "Everything here is real data: eSett settlement volumes and day-ahead prices for 2025.",
      "The new bottleneck is local grid capacity: connection queues and capacity maps (links on the right, not copied).",
    ],
    twin: [
      "Zoom in to one real substation in Bromma: the buildings, streets and substation position come from OpenStreetMap; the cables are synthetic and labelled as such.",
      "Drag across the year strip: the map is lit by the real sun over Bromma, and cables colour with their loading hour by hour.",
      "The map opens on the most loaded hour of 2025. Switch to 'No control': the transformer runs in reverse above its rating. Then 'With envelopes': same houses, limits never exceeded.",
    ],
    compare: [
      "The headline: the worst-case study stops at the first number; dynamic envelopes connect the second on the same transformer.",
      "It is not a free lunch: the newcomers lose a few per cent of their solar energy, only on clear middays (see the heatmap).",
      "Every envelope is checked hour by hour with the full nonlinear power flow: zero violations.",
      "The tornado shows what really matters: the transformer rating and the inverter sizing, not the voltage assumptions.",
    ],
    request: [
      "Click a house and ask for 10 kW of solar plus an EV charger: the static study answers yes or no; the envelope answers 'yes, with this hourly limit and this risk'.",
      "The evaluation runs live: 11 weather years, a nonlinear check and the cost at real SE3 prices, in about a second.",
      "Switch the grid to 2030: the same request now gets more curtailment because earlier customers used the headroom first.",
    ],
    scenarios: [
      "Electrification to 2030: every cell is a simulated year with more solar, EVs and heat pumps.",
      "Green-and-yellow is the gap the envelopes buy: the static study already needs a reinforcement there.",
      "Heat pumps are firm demand: past a point no control helps, which is where reinforcement is genuinely needed.",
    ],
    method: [
      "Where every number comes from: live status of each API, the cached files with hashes, and every assumption with its source or marked as ours.",
      "Validation: the fast power flow matches pandapower to 1e-12 pu; timestamps, diffuse irradiance and the household load shape are checked against real data.",
      "Limitations are listed openly: perfect foresight, hourly resolution, balanced three-phase model.",
    ],
  },
  es: {
    national: [
      "Empezar a escala nacional: Suecia produce en el norte (hidráulica, eólica) y consume en el sur; el precio sube de SE1 a SE4.",
      "Todo aquí es dato real: volúmenes de liquidación de eSett y precios del mercado diario de 2025.",
      "El nuevo cuello de botella es la capacidad de la red local: colas de conexión y mapas de capacidad (enlazados, no copiados).",
    ],
    twin: [
      "Acercarse a una subestación real de Bromma: edificios, calles y posición de la subestación vienen de OpenStreetMap; los cables son sintéticos y así se indica.",
      "Arrastrar sobre la franja del año: el mapa se ilumina con el sol real de Bromma y los cables se colorean con su carga hora a hora.",
      "El mapa se abre en la hora más cargada de 2025. Cambiar a 'Sin control': el transformador trabaja en inverso por encima de su capacidad. Luego 'Con envolventes': mismas casas, nunca se superan los límites.",
    ],
    compare: [
      "El titular: el estudio de peor caso se detiene en la primera cifra; las envolventes dinámicas conectan la segunda con el mismo transformador.",
      "No es gratis: los nuevos clientes pierden un pequeño porcentaje de su energía solar, solo en mediodías despejados (ver el mapa de calor).",
      "Cada envolvente se verifica hora a hora con el flujo de potencia no lineal completo: cero violaciones.",
      "El tornado muestra lo que de verdad importa: la potencia del transformador y el dimensionado de los inversores, no los supuestos de tensión.",
    ],
    request: [
      "Hacer clic en una vivienda y pedir 10 kW de solar más un cargador de VE: el estudio estático responde sí o no; la envolvente responde 'sí, con este límite horario y este riesgo'.",
      "La evaluación corre en vivo: 11 años meteorológicos, una verificación no lineal y el coste a precios reales de SE3, en torno a un segundo.",
      "Cambiar la red a 2030: la misma solicitud recibe más recorte porque los clientes anteriores usaron antes el margen.",
    ],
    scenarios: [
      "Electrificación hacia 2030: cada celda es un año simulado con más solar, VE y bombas de calor.",
      "Las celdas intermedias son el margen que compran las envolventes: el estudio estático ya exige reforzar ahí.",
      "Las bombas de calor son demanda firme: a partir de cierto punto ningún control ayuda, y ahí sí hace falta reforzar.",
    ],
    method: [
      "De dónde sale cada número: estado en vivo de cada API, los archivos en caché con su hash y cada supuesto con su fuente o marcado como nuestro.",
      "Validación: el flujo de potencia rápido coincide con pandapower a 1e-12 pu; las marcas de tiempo, la irradiancia difusa y la forma del consumo se contrastan con datos reales.",
      "Las limitaciones se muestran abiertamente: previsión perfecta, resolución horaria, modelo trifásico equilibrado.",
    ],
  },
};
