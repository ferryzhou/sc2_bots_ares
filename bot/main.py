from typing import Optional

from ares import AresBot
from ares.behaviors.macro import AutoSupply, Mining, SpawnController, ProductionController
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as UnitID

# this will be used for ares SpawnController behavior
ARMY_COMPS: dict[Race, dict] = {
    Race.Protoss: {
        UnitID.STALKER: {"proportion": 0.4, "priority": 0},
        UnitID.ZEALOT: {"proportion": 0.3, "priority": 1},
        UnitID.IMMORTAL: {"proportion": 0.15, "priority": 2},
        UnitID.SENTRY: {"proportion": 0.1, "priority": 3},
        UnitID.OBSERVER: {"proportion": 0.05, "priority": 4},
    },
    Race.Terran: {
        UnitID.MARINE: {"proportion": 1.0, "priority": 0},
    },
    Race.Zerg: {
        UnitID.ROACH: {"proportion": 1.0, "priority": 0},
    },
    # Example if using more than one unit
    # proportion's add up to 1.0 with 0 being highest priority and 10 lowest
    # Race.Zerg: {
    #     UnitID.HYDRALISK: {"proportion": 0.15, "priority": 0},
    #     UnitID.ROACH: {"proportion": 0.8, "priority": 1},
    #     UnitID.ZERGLING: {"proportion": 0.05, "priority": 2},
    # },
}

class MyBot(AresBot):
    def __init__(self, game_step_override: Optional[int] = None):
        """Initiate custom bot

        Parameters
        ----------
        game_step_override :
            If provided, set the game_step to this value regardless of how it was
            specified elsewhere
        """
        super().__init__(game_step_override)

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)

        self._macro()

        pass

    def _macro(self) -> None:
        # MINE
        # ares-sc2 Mining behavior
        # https://aressc2.github.io/ares-sc2/api_reference/behaviors/macro_behaviors.html#ares.behaviors.macro.mining.Mining
        self.register_behavior(Mining())

        # MAKE SUPPLY
        # ares-sc2 AutoSupply
        # https://aressc2.github.io/ares-sc2/api_reference/behaviors/macro_behaviors.html#ares.behaviors.macro.auto_supply.AutoSupply
        if self.build_order_runner.build_completed:
            self.register_behavior(AutoSupply(base_location=self.start_location))

        # BUILD ARMY
        # ares-sc2 SpawnController
        # https://aressc2.github.io/ares-sc2/api_reference/behaviors/macro_behaviors.html#ares.behaviors.macro.spawn_controller.SpawnController

        # production controller
        self.register_behavior(
            ProductionController(ARMY_COMPS[self.race], self.start_location)
        )
        self.register_behavior(SpawnController(ARMY_COMPS[self.race]))

    """
    Can use `python-sc2` hooks as usual, but make a call the inherited method in the superclass
    Examples:
    """
    # async def on_start(self) -> None:
    #     await super(MyBot, self).on_start()
    #
    #     # on_start logic here ...
    #
    # async def on_end(self, game_result: Result) -> None:
    #     await super(MyBot, self).on_end(game_result)
    #
    #     # custom on_end logic here ...
    #
    # async def on_building_construction_complete(self, unit: Unit) -> None:
    #     await super(MyBot, self).on_building_construction_complete(unit)
    #
    #     # custom on_building_construction_complete logic here ...
    #
    # async def on_unit_created(self, unit: Unit) -> None:
    #     await super(MyBot, self).on_unit_created(unit)
    #
    #     # custom on_unit_created logic here ...
    #
    # async def on_unit_destroyed(self, unit_tag: int) -> None:
    #     await super(MyBot, self).on_unit_destroyed(unit_tag)
    #
    #     # custom on_unit_destroyed logic here ...
    #
    # async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
    #     await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)
    #
    #     # custom on_unit_took_damage logic here ...
