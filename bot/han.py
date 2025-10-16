import sc2
from sc2 import maps
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2, Point3

from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.ids.ability_id import AbilityId
import random
from sc2.ids.upgrade_id import UpgradeId
import math
import time

class HanBot(BotAI):
    def __init__(self):
        super().__init__()
        self.race = Race.Terran
        self.retreating_units = {}  # Initialize retreating_units dictionary
        self.historical_retreating_units = {}  # Initialize retreating_units dictionary
        self.defender_worker_tags = set()
        self.waiting_for_base_expansion = False
        self.scout_tags = set()  # Track units assigned to scouting
        self.scouted_locations = {}  # Track when locations were last scouted (location -> time)
        # Any other initialization you need
    
    async def on_step(self, iteration):
        await self.manage_army()
        await self.build_supply_depot_if_needed()
        await self.manage_economy()
        await self.manage_scouting()
        if self.waiting_for_base_expansion:
            return
        if iteration % 15 == 0:  # Every 10 iterations
            print(f"iteration {iteration}")
            await self.manage_production()

    async def manage_economy(self):
        await self.distribute_workers()
        await self.manage_mules()
        await self.train_workers_if_needed()
        await self.manage_base_expansion()
    
    async def manage_base_expansion(self):
        if self.should_expand_base():
            if self.can_afford(UnitTypeId.COMMANDCENTER):
                await self.expand_base()
            else:
                self.waiting_for_base_expansion = True
        else:
            self.waiting_for_base_expansion = False

    async def manage_mules(self):
        # Transform Command Center to Orbital Command if possible
        for cc in self.structures(UnitTypeId.COMMANDCENTER).ready.idle:
            if self.can_afford(UnitTypeId.ORBITALCOMMAND):
                cc(AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND)

        """Manage MULE production and optimal mineral mining."""
        # Check for Orbital Commands
        for oc in self.structures(UnitTypeId.ORBITALCOMMAND).ready:
            # Only call down MULE if we have enough energy
            if oc.energy < 50:
                return

            # Find the best mineral field to drop MULE on
            mineral_fields = self.mineral_field.closer_than(10, oc)
            if mineral_fields:
                # Prioritize mineral fields with more minerals remaining
                best_mineral = max(
                    mineral_fields,
                    key=lambda mineral: (
                        mineral.mineral_contents,
                        -oc.distance_to(mineral)  # Secondary sort by distance
                    )
                )
                # Call down MULE
                oc(AbilityId.CALLDOWNMULE_CALLDOWNMULE, best_mineral)

    async def train_workers_if_needed(self):
        # Modified to account for MULE income
        mule_count = self.units(UnitTypeId.MULE).amount
        effective_worker_count = self.workers.amount + (mule_count * 4)  # Each MULE mines like ~4 SCVs
        
        if effective_worker_count >= 80:
            return
        
        if effective_worker_count >= 20 * self.townhalls.ready.amount:
            return
        
        for cc in self.townhalls.ready.idle:
            if self.can_afford(UnitTypeId.SCV) and self.supply_left > 0:
                cc.train(UnitTypeId.SCV)

    async def manage_production(self):
        # print(f"manage_production")
        await self.build_gas_if_needed()
        await self.build_structure_if_needed(UnitTypeId.FACTORY)
        await self.build_structure_if_needed(UnitTypeId.BARRACKS)
        await self.build_structure_if_needed(UnitTypeId.STARPORT)
        await self.build_structure_if_needed(UnitTypeId.ENGINEERINGBAY)
        await self.append_addons()
        await self.upgrade_army()
        await self.train_military_units()

    async def manage_army(self):
        # Get all military units
        military_units = self.units(UnitTypeId.MARINE) | self.units(UnitTypeId.MARAUDER)
        tanks = self.units(UnitTypeId.SIEGETANK) | self.units(UnitTypeId.SIEGETANKSIEGED)
        medivacs = self.units(UnitTypeId.MEDIVAC)
        ravens = self.units(UnitTypeId.RAVEN)
        
        # Filter out scouts from army management
        military_units = military_units.filter(lambda u: u.tag not in self.scout_tags)
        medivacs = medivacs.filter(lambda u: u.tag not in self.scout_tags)
        ravens = ravens.filter(lambda u: u.tag not in self.scout_tags)
        
        await self.manage_medivacs(medivacs, military_units)
        await self.manage_ravens(ravens, military_units)

        if self.detected_cheese():
            print(f"detected cheese")
            await self.handle_early_game_defense(military_units, tanks)
            return
        
        # Normal army management for mid/late game
        if self.townhalls:
            for base in self.townhalls:
                nearby_enemies = self.enemy_units.filter(
                    lambda unit: unit.distance_to(base) < 30
                )
                if nearby_enemies:
                    print(f"Defending against enemies near base!")
                    await self.execute_attack(military_units, tanks)
                    return

        if not self.should_attack():
            await self.rally(military_units, tanks)
            return

        # print(f"attacking")
        await self.execute_attack(military_units, tanks)

    async def manage_scouting(self):
        """Manage scouting in late game to gather intelligence on enemy positions and expansions."""
        # Only scout in late game (after 5 minutes or when we have sufficient army)
        #if self.time < 300 and self.get_military_supply() < 30:
        #    return
        
        # Determine desired number of scouts based on game time
        desired_scouts = 1 if self.time < 600 else 2  # 1 scout before 10 min, 2 after
        
        # Clean up scout tags for dead units
        self.scout_tags = {tag for tag in self.scout_tags if self.units.find_by_tag(tag)}
        
        current_scouts = len(self.scout_tags)
        
        # Assign new scouts if needed
        if current_scouts < desired_scouts:
            # Prefer Medivacs or Ravens for scouting (they can fly)
            potential_scouts = (
                self.units(UnitTypeId.MEDIVAC).idle | 
                self.units(UnitTypeId.RAVEN).idle
            ).filter(lambda u: u.tag not in self.scout_tags)
            
            # If no flying units available, use Marines
            if not potential_scouts:
                marines = self.units(UnitTypeId.MARINE).filter(
                    lambda u: u.tag not in self.scout_tags and u.tag not in self.retreating_units
                )
                # Only take a marine if we have plenty
                if len(marines) > 15:
                    potential_scouts = marines.take(1)
            
            # Assign scouts
            for scout in potential_scouts.take(desired_scouts - current_scouts):
                self.scout_tags.add(scout.tag)
                print(f"Assigned {scout.type_id} as scout")
        
        # Manage existing scouts
        for scout_tag in list(self.scout_tags):
            scout = self.units.find_by_tag(scout_tag)
            if not scout:
                self.scout_tags.remove(scout_tag)
                continue
            
            # If scout is under attack and low health, retreat it
            if scout.health_percentage < 0.3:
                nearby_enemies = self.enemy_units.filter(lambda e: e.distance_to(scout) < 10)
                if nearby_enemies:
                    retreat_pos = scout.position.towards(self.start_location, 10)
                    scout.move(retreat_pos)
                    continue
            
            # Get scouting targets
            scout_targets = self.get_scout_targets()
            
            if scout_targets:
                # Find the nearest unscouted or least recently scouted location
                target = min(
                    scout_targets,
                    key=lambda loc: (
                        self.scouted_locations.get(loc, 0),  # Prioritize never-scouted locations
                        scout.distance_to(loc)  # Then by distance
                    )
                )
                
                # Move scout to target
                if scout.distance_to(target) > 3:
                    scout.move(target)
                else:
                    # Mark location as scouted
                    self.scouted_locations[target] = self.time
                    print(f"Scout reached {target}, marking as scouted")
    
    def get_scout_targets(self):
        """Get list of locations to scout (enemy expansions and key map locations)."""
        targets = []
        
        # Add enemy start location
        if self.enemy_start_locations:
            targets.append(self.enemy_start_locations[0])
        
        # Add all expansion locations (to find enemy expansions)
        for exp_loc in self.expansion_locations_list:
            # Skip our own bases
            if not any(th.distance_to(exp_loc) < 10 for th in self.townhalls):
                targets.append(exp_loc)
        
        # Add map center for general scouting
        targets.append(self.game_info.map_center)
        
        # Filter out recently scouted locations (within last 2 minutes)
        current_time = self.time
        targets = [
            loc for loc in targets 
            if current_time - self.scouted_locations.get(loc, 0) > 120
        ]
        
        return targets

    def detected_cheese(self):
        if self.time >= 180: # First 3 minutes
            return False
        
        th = self.start_location

        # Check for both enemy units and structures
        nearby_enemies = self.enemy_units.filter(
            lambda unit: (
                unit.distance_to(th) < 30 and  # Close to our base
                not unit.is_structure and      # Not a building
                unit.type_id not in {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}  # Not a worker
            )
        )
        
        nearby_structures = self.enemy_structures.filter(
            lambda structure: structure.distance_to(th) < 30  # Close to our base
        )
        
        # If we spot enemy units or structures near our base
        if nearby_enemies or nearby_structures:
            return True
        
        return False

    async def handle_early_game_defense(self, military_units, tanks):
        """Handle early game defense while maintaining economy and counter-attacking."""
        for th in self.townhalls:
            # Check for both enemy units and structures
            nearby_enemies = self.enemy_units.filter(
                lambda unit: (
                    unit.distance_to(th) < 30 and
                    not unit.is_structure and
                    unit.type_id not in {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
                )
            )
            
            # Separate workers from other enemy units
            nearby_enemy_workers = self.enemy_units.filter(
                lambda unit: (
                    unit.distance_to(th) < 30 and
                    unit.type_id in {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
                )
            )
            
            # Identify offensive structures (those that can attack)
            offensive_structures = self.enemy_structures.filter(
                lambda structure: (
                    structure.distance_to(th) < 30 and
                    structure.type_id in {
                        UnitTypeId.PHOTONCANNON, UnitTypeId.SPINECRAWLER, 
                        UnitTypeId.SPORECRAWLER, UnitTypeId.BUNKER,
                        UnitTypeId.PLANETARYFORTRESS
                    }
                )
            )
            
            # Other nearby structures
            other_structures = self.enemy_structures.filter(
                lambda structure: (
                    structure.distance_to(th) < 30 and
                    structure.type_id not in {
                        UnitTypeId.PHOTONCANNON, UnitTypeId.SPINECRAWLER, 
                        UnitTypeId.SPORECRAWLER, UnitTypeId.BUNKER,
                        UnitTypeId.PLANETARYFORTRESS
                    }
                )
            )
            
            # If we spot any threats near our base
            if nearby_enemies or nearby_enemy_workers or offensive_structures or other_structures:
                print(f"Early game threat detected! Defending base at {th.position}")
                
                # Calculate military and enemy power
                military_power = len(military_units) + len(tanks) * 2
                enemy_power = (len(nearby_enemies) + len(offensive_structures) * 3 + 
                             len(nearby_enemy_workers) + len(other_structures))
                
                # Worker defense allocation
                nearby_workers = self.workers.filter(lambda w: w.distance_to(th) < 10)
                current_defender_tags = getattr(self, 'defender_worker_tags', set())
                
                # Calculate how many workers we need
                base_defender_count = min(8, enemy_power)
                
                print(f"base_defender_count: {base_defender_count}")
                # Adjust defender count based on our worker count to maintain economy
                total_workers = len(self.workers)
                if total_workers < 12:  # Early game
                    base_defender_count = min(base_defender_count, 8)  # Limit early pulls
                elif total_workers > 20:  # More established
                    base_defender_count = min(base_defender_count + 2, 12)  # Can pull more
                
                # Select defenders
                defender_workers = []
                mining_workers = []
                
                # First, check current defenders and keep them if still needed
                current_defenders = [w for w in nearby_workers if w.tag in current_defender_tags]
                remaining_slots = base_defender_count - len(current_defenders)
                
                # Add new defenders if needed
                if remaining_slots > 0:
                    potential_new_defenders = [w for w in nearby_workers if w.tag not in current_defender_tags]
                    new_defenders = potential_new_defenders[:remaining_slots]
                    defender_workers = current_defenders + new_defenders
                else:
                    defender_workers = current_defenders[:base_defender_count]
                
                # Update defender tags
                self.defender_worker_tags = {w.tag for w in defender_workers}
                
                # Prioritize targets for military units
                for unit in military_units:
                    # Priority 1: Offensive structures
                    if offensive_structures:
                        closest_threat = offensive_structures.closest_to(unit)
                        unit.attack(closest_threat)
                        continue
                        
                    # Priority 2: Enemy workers building structures
                    if nearby_enemy_workers:
                        building_workers = [w for w in nearby_enemy_workers]
                        if building_workers:
                            closest_worker = min(building_workers, key=lambda w: w.distance_to(unit))
                            unit.attack(closest_worker)
                            continue
                    
                    # Priority 3: Other enemy units
                    if nearby_enemies:
                        closest_enemy = nearby_enemies.closest_to(unit)
                        unit.attack(closest_enemy)
                        continue
                        
                    # Priority 4: Other structures
                    if other_structures:
                        closest_structure = other_structures.closest_to(unit)
                        unit.attack(closest_structure)
                        continue
                        
                    # Priority 5: Remaining enemy workers
                    if nearby_enemy_workers:
                        closest_worker = nearby_enemy_workers.closest_to(unit)
                        unit.attack(closest_worker)
                
                # Assign defender workers with similar priority
                for worker in nearby_workers:
                    if worker.tag in self.defender_worker_tags:
                        if offensive_structures:
                            worker.attack(offensive_structures.closest_to(worker))
                        elif nearby_enemy_workers:
                            closest_building_worker = min(
                                [w for w in nearby_enemy_workers],
                                key=lambda w: w.distance_to(worker)
                            )
                            worker.attack(closest_building_worker)
                        elif nearby_enemies:
                            worker.attack(nearby_enemies.closest_to(worker))
                        elif other_structures:
                            worker.attack(other_structures.closest_to(worker))
                    else:
                        if worker.is_attacking:
                            closest_mineral = self.mineral_field.closest_to(worker)
                            worker.gather(closest_mineral)
                
                return True
                
        # Clear defender tags when no threats
        if hasattr(self, 'defender_worker_tags'):
            for worker in self.workers:
                if worker.tag in self.defender_worker_tags and worker.is_attacking:
                    closest_mineral = self.mineral_field.closest_to(worker)
                    worker.gather(closest_mineral)
            self.defender_worker_tags = set()
            
        return False

    async def rally(self, military_units, tanks):
        """Execute defensive positioning by rallying units to a defensive position."""
        if not (military_units or tanks):
            return
            
        # Determine rally point - closest base to map center or main base ramp
        if self.townhalls.ready and self.townhalls.ready.amount > 1:
            forward_base = self.townhalls.ready.closest_to(self.game_info.map_center)
            rally_point = forward_base.position.towards(self.game_info.map_center, 8)
        else:
            rally_point = self.main_base_ramp.top_center
        
        # Move military units to rally point with slight spread
        for unit in military_units:
            # Create slight offset for each unit to prevent stacking
            offset = Point2((hash(unit.tag) % 3 - 1, hash(unit.tag) // 3 % 3 - 1))
            defensive_pos = rally_point + offset * 2
            unit.attack(defensive_pos)
        
        # Position tanks at rally point
        for tank in tanks:
            if tank.type_id == UnitTypeId.SIEGETANKSIEGED:
                if tank.distance_to(rally_point) > 7:  # If too far from rally, unsiege
                    tank(AbilityId.UNSIEGE_UNSIEGE)
            else:  # Regular tank
                if tank.distance_to(rally_point) <= 5:  # If at rally point, siege up
                    tank(AbilityId.SIEGEMODE_SIEGEMODE)
                else:  # Move to rally point
                    tank.move(rally_point)

    async def execute_attack(self, military_units, tanks):
        """Execute attack logic with retreat time limits."""
        current_time = time.time()
        
        # Clean up old retreat timers
        self.retreating_units = {
            unit_tag: retreat_time 
            for unit_tag, retreat_time in self.retreating_units.items() 
            if current_time - retreat_time < 10
        }
        
        # Filter out eggs and overlords from enemy units
        enemy_units = self.enemy_units.filter(
            lambda unit: unit.type_id not in {
                UnitTypeId.EGG,
                UnitTypeId.LARVA,
                UnitTypeId.OVERLORD,
                UnitTypeId.OVERLORDCOCOON,
                UnitTypeId.OVERSEER,
                UnitTypeId.OVERSEERSIEGEMODE,
                UnitTypeId.CHANGELING,
                UnitTypeId.CHANGELINGMARINE,
                UnitTypeId.CHANGELINGMARINESHIELD,
                UnitTypeId.CHANGELINGZEALOT,
                UnitTypeId.CHANGELINGZERGLING,
                UnitTypeId.CHANGELINGZERGLINGWINGS
            }
        )
        
        # Identify offensive structures (those that can attack)
        offensive_structures = self.enemy_structures.filter(
            lambda structure: structure.can_attack or structure.type_id in {
                # Protoss
                UnitTypeId.PHOTONCANNON, UnitTypeId.SHIELDBATTERY,
                # Terran
                UnitTypeId.MISSILETURRET, UnitTypeId.BUNKER, UnitTypeId.PLANETARYFORTRESS,
                # Zerg
                UnitTypeId.SPINECRAWLER, UnitTypeId.SPORECRAWLER
            }
        )
        
        # Combine enemy units with offensive structures for targeting
        enemy_threats = enemy_units + offensive_structures
        
        # Other enemy structures
        other_structures = self.enemy_structures.filter(
            lambda structure: structure not in offensive_structures
        )
        
        enemy_start = self.enemy_start_locations[0]
        
        # Rest of the attack logic for military units
        for unit in military_units:
            # Find nearby enemies including offensive structures
            nearby_threats = enemy_threats.filter(
                lambda enemy: enemy.distance_to(unit) < 15
            )
            
            # Check if unit is currently retreating
            if unit.tag in self.retreating_units:
                retreat_time = self.retreating_units[unit.tag]
                if current_time - retreat_time >= 10:  # 10 seconds retreat limit
                    del self.retreating_units[unit.tag]
            
            if nearby_threats:
                closest_threat = nearby_threats.closest_to(unit)
                
                # Handle unit actions based on health
                if unit.health_percentage < 0.4 and unit.tag not in self.retreating_units and unit.tag not in self.historical_retreating_units:
                    # Start retreat
                    retreat_pos = unit.position.towards(self.start_location, 20)
                    unit.move(retreat_pos)
                    self.retreating_units[unit.tag] = current_time
                    self.historical_retreating_units[unit.tag] = current_time
                elif unit.tag not in self.retreating_units:
                    # Normal combat micro
                    if unit.ground_range > 1:  # Ranged unit
                        if unit.weapon_cooldown > 0:  # If we can't shoot, kite back
                            retreat_pos = unit.position.towards(closest_threat.position, -2)
                            unit.move(retreat_pos)
                        else:  # If we can shoot, attack
                            unit.attack(closest_threat)
                    else:  # Melee units
                        unit.attack(closest_threat)
            
            elif unit.tag not in self.retreating_units:
                # No nearby threats, attack other structures or enemy base
                if other_structures:
                    closest_structure = other_structures.closest_to(unit)
                    unit.attack(closest_structure)
                else:
                    unit.attack(enemy_start)
        
        # Handle tanks with similar priority
        for tank in tanks:
            target = enemy_start
            if nearby_threats:
                target = nearby_threats.closest_to(tank)
            elif other_structures:
                target = other_structures.closest_to(tank)
            
            await self.manage_attacking_tank(tank, target)

        await self.manage_attacking_ravens(self.units(UnitTypeId.RAVEN), enemy_units)

    async def manage_attacking_tank(self, tank, target):
        """Manage tank positioning and siege mode during attacks."""
        enemy_distance = target.distance_to(tank)
        
        if tank.type_id == UnitTypeId.SIEGETANK:
            if enemy_distance < 15:  # Optimal siege range
                tank(AbilityId.SIEGEMODE_SIEGEMODE)
            else:
                # Move closer while avoiding getting too close
                desired_position = target.position.towards(tank.position, 14)
                tank.move(desired_position)
        elif tank.type_id == UnitTypeId.SIEGETANKSIEGED:
            if enemy_distance > 20:  # Enemy moved out of range
                tank(AbilityId.UNSIEGE_UNSIEGE)
            # Otherwise stay sieged and let default attack handle it
    

    async def manage_attacking_ravens(self, ravens, enemy_units):
        """Manage Raven auto-turrets during attacks."""
        if not ravens or not enemy_units:
            return
        # Handle Raven auto-turrets
        for raven in ravens:
            if raven.energy >= 50:  # Auto-Turret costs 50 energy
                nearby_enemies = enemy_units.filter(
                    lambda unit: unit.distance_to(raven) < 15
                )
                
                if nearby_enemies:
                    # Find the best position for the turret
                    if len(nearby_enemies) >= 3:
                        # Drop at center of enemy cluster
                        turret_position = nearby_enemies.center
                    else:
                        # Drop at closest enemy
                        turret_position = nearby_enemies.closest_to(raven).position
                    
                    # Ensure the position is on valid terrain
                    if self.in_pathing_grid(turret_position):
                        raven(AbilityId.BUILDAUTOTURRET_AUTOTURRET, turret_position)
                        # print(f"Raven {raven.tag} dropping turret during attack")
    

        # Get closest enemy unit for each raven    

    async def manage_medivacs(self, medivacs, military_units):
        """Manage medivac movement to follow army units."""
        if not medivacs or not military_units:
            return

        enemies = self.enemy_units | self.enemy_structures
        if not enemies:
            # If no enemies, follow army center as before
            center = military_units.center
            for medivac in medivacs:
                if medivac.distance_to(center) > 5:
                    medivac.move(center)
            return

        # Get units that are close to enemies
        forward_units = military_units.filter(
            lambda unit: enemies.closest_to(unit).distance_to(unit) < 15
        )

        if not forward_units:
            # If no units close to enemies, follow army center
            center = military_units.center
            for medivac in medivacs:
                if medivac.distance_to(center) > 5:
                    medivac.move(center)
            return

        # Assign each medivac to a random forward unit
        for medivac in medivacs:
            target_unit = random.choice(forward_units)
            if medivac.distance_to(target_unit) > 3:
                medivac.move(target_unit.position)

    async def manage_ravens(self, ravens, military_units):
        """Manage raven movement to follow army units and use abilities."""
        if not ravens or not military_units:
            return

        enemies = self.enemy_units | self.enemy_structures
        if not enemies:
            # If no enemies, follow army center
            center = military_units.center
            for raven in ravens:
                if raven.distance_to(center) > 7:
                    raven.move(center)
            return

        # Get units that are close to enemies
        forward_units = military_units.filter(
            lambda unit: enemies.closest_to(unit).distance_to(unit) < 15
        )

        if not forward_units:
            # If no units close to enemies, follow army center
            center = military_units.center
            for raven in ravens:
                if raven.distance_to(center) > 7:
                    raven.move(center)
            return

        # Assign each raven to a random forward unit
        for raven in ravens:
            target_unit = random.choice(forward_units)
            if raven.distance_to(target_unit) > 5:
                raven.move(target_unit.position)

    async def build_supply_depot_if_needed(self):
        if self.supply_left < 6 * self.townhalls.amount:
            max_concurrent = 2 if self.townhalls.ready.amount > 1 else 1
            pending_depots = self.already_pending(UnitTypeId.SUPPLYDEPOT)
            near_position = self.start_location
            if self.townhalls:
                near_position = self.townhalls.first.position
            
            while (
                pending_depots < max_concurrent 
                and self.can_afford(UnitTypeId.SUPPLYDEPOT)
            ):
                await self.build(UnitTypeId.SUPPLYDEPOT, near=near_position)
                pending_depots += 1

        # Lower completed supply depots
        for depot in self.structures(UnitTypeId.SUPPLYDEPOT).ready:
            depot(AbilityId.MORPH_SUPPLYDEPOT_LOWER)
        
        # Raise if enemies nearby
        for depot in self.structures(UnitTypeId.SUPPLYDEPOTLOWERED).ready:
            if self.enemy_units:
                closest_enemy = self.enemy_units.closest_to(depot)
                if closest_enemy.distance_to(depot) < 10:
                    depot(AbilityId.MORPH_SUPPLYDEPOT_RAISE)

    def get_max_refineries(self):
        if self.get_total_structure_count(UnitTypeId.BARRACKS) == 0:
            return 0
        if self.townhalls.ready.amount == 1:
            return 1
        if self.townhalls.ready.amount == 2:
            return 4
        return self.townhalls.ready.amount * 1.2 + 2

    async def build_gas_if_needed(self):
        if self.get_total_structure_count(UnitTypeId.REFINERY) >= self.get_max_refineries():
            return
        if self.can_afford(UnitTypeId.REFINERY):
            await self.build_one_gas()

    async def build_one_gas(self):
        for th in self.townhalls.ready:
            vgs = self.vespene_geyser.closer_than(10, th)
            for vg in vgs:
                if await self.can_place_single(UnitTypeId.REFINERY, vg.position):
                    workers = self.workers.gathering
                    if workers:
                        worker = workers.closest_to(vg)
                        worker.build_gas(vg)
                        return

    async def build_structure_if_needed(self, unit_type):
        if not self.can_afford(unit_type):
            return
        if self.get_total_structure_count(unit_type) >= self.get_max_structure_count(unit_type):
            return
        await self.build_structure(unit_type)
    
    def get_max_structure_count(self, unit_type):
        if unit_type == UnitTypeId.BARRACKS:
            return self.get_max_barracks()
        if unit_type == UnitTypeId.FACTORY:
            return self.get_max_factories()
        if unit_type == UnitTypeId.STARPORT:
            return self.get_max_starports()
        return 0

    async def build_structure(self, unit_type):
        if not self.can_afford(unit_type):
            return
        # Get main base and its position
        cc = self.townhalls.first
        base_pos = cc.position
        
        # Try primary placement method
        pos = await self.find_placement(
            unit_type,
            near_position=base_pos,
            min_distance=6,
            max_distance=25,
            addon_space=True
        )
        
        if pos:
            print(f"Building {unit_type} at position {pos}")
            await self.build(unit_type, near=pos)
        else:
            # Fallback method 1: Try direct placement
            print(f"Fallback: Using direct placement for {unit_type}")
            potential_positions = [
                base_pos.towards(self.game_info.map_center, 8),
                base_pos.towards(self.game_info.map_center, 12),
                base_pos.towards(self.game_info.map_center, 16)
            ]
            
            for fallback_pos in potential_positions:
                if await self.can_place(unit_type, fallback_pos):
                    await self.build(unit_type, near=fallback_pos)
                    return
            
            # Fallback method 2: Just try the standard build method near base
            await self.build(unit_type, near=base_pos)


    def get_max_barracks(self):
        if self.townhalls.ready.amount == 1 and self.get_total_structure_count(UnitTypeId.BARRACKS) < 2:
            return 1
        barracks_by_workers = self.workers.amount // 6
        maxinum = 12
        if self.get_max_factories() == 0:
            maxinum = 3
        if self.structures(UnitTypeId.FACTORY).ready.amount == 0:
            maxinum = 3
        if self.get_max_factories() == 1:
            maxinum = 6
        return min(barracks_by_workers, maxinum)

    def get_max_factories(self):
        if not self.structures(UnitTypeId.BARRACKS).ready:
            return 0
        #if self.get_military_supply() < 10:
        #    return 0
        if self.townhalls.ready.amount <= 2:
            return 1
        if self.townhalls.ready.amount <= 3:
            return 2
        return 3


    def get_max_starports(self):
        if not self.structures(UnitTypeId.FACTORY).ready:
            return 0
        if self.get_military_supply() < 10:
            return 0
        return 2


    def get_max_engineering_bays(self):
        if not self.structures(UnitTypeId.FACTORY).ready:
            return 0
        if self.get_military_supply() < 10:
            return 0
        return 2

    def get_desired_units(self, unit_type):
        if unit_type == UnitTypeId.MARINE:
            return self.get_desired_marines()
        if unit_type == UnitTypeId.MARAUDER:
            return self.get_desired_marauders()
        if unit_type == UnitTypeId.SIEGETANK:
            return self.get_desired_tanks()
        if unit_type == UnitTypeId.MEDIVAC:
            return self.get_desired_medivacs()
        if unit_type == UnitTypeId.RAVEN:
            return self.get_desired_ravens()
        return 0

    def train_units_if_needed(self, unit_type):
        desired_units = self.get_desired_units(unit_type)
        print(f"desired units for {unit_type}: {desired_units}")
        if desired_units > 0:
            self.train_units(unit_type)

    def train_units(self, unit_type):
        if unit_type == UnitTypeId.MARINE:
            self.train_marines()
        if unit_type == UnitTypeId.MARAUDER:
            self.train_marauders()
        if unit_type == UnitTypeId.SIEGETANK:
            self.train_tanks()
        if unit_type == UnitTypeId.MEDIVAC:
            self.train_medivacs()
        if unit_type == UnitTypeId.RAVEN:
            self.train_ravens()

    def get_total_units_count(self, unit_type):
        return self.units(unit_type).amount + self.already_pending(unit_type)

    def get_desired_marines(self):
        if not self.structures(UnitTypeId.BARRACKS).ready:
            return 0
        marine_count = self.get_total_units_count(UnitTypeId.MARINE)
        marauder_count = self.get_total_units_count(UnitTypeId.MARAUDER)
        return marauder_count - marine_count + 8

    def get_desired_marauders(self):
        if not self.structures(UnitTypeId.BARRACKS).ready:
            return 0
        marauder_count = self.get_total_units_count(UnitTypeId.MARAUDER)
        marine_count = self.get_total_units_count(UnitTypeId.MARINE)
        return marine_count - marauder_count + 2
   
    def get_desired_tanks(self):
        if not self.structures(UnitTypeId.FACTORY).ready:
            return 0
        if self.get_military_supply() < 5:
            return 0
        return 8

    def get_desired_medivacs(self):
        if not self.structures(UnitTypeId.STARPORT).ready:
            return 0

        if self.get_military_supply() < 10:
            return 0
        
        ground_units = self.get_total_units_count(UnitTypeId.MARINE) + self.get_total_units_count(UnitTypeId.MARAUDER)
        desired_medivacs = ground_units // 8  # One medivac for every 8 ground units

        return desired_medivacs

    def get_desired_ravens(self):
        if not self.structures(UnitTypeId.STARPORT).ready:
            return 0
        if self.get_military_supply() < 10:
            return 0
        return 2

    def train_tanks(self):
        for factory in self.structures(UnitTypeId.FACTORY).ready.idle:
            if factory.has_add_on:
                if factory.add_on_tag in self.structures(UnitTypeId.FACTORYTECHLAB).tags:
                    if self.can_afford(UnitTypeId.SIEGETANK):
                        factory.train(UnitTypeId.SIEGETANK)
                    else:
                        print(f"cannot afford tanks")

    def train_medivacs(self):
        for starport in self.structures(UnitTypeId.STARPORT).ready.idle:
            if self.can_afford(UnitTypeId.MEDIVAC):
                starport.train(UnitTypeId.MEDIVAC)

    def train_ravens(self):
        for starport in self.structures(UnitTypeId.STARPORT).ready.idle:
            if starport.has_add_on:
                if starport.add_on_tag in self.structures(UnitTypeId.STARPORTTECHLAB).tags:
                    if self.can_afford(UnitTypeId.RAVEN):
                        starport.train(UnitTypeId.RAVEN)

    def train_marines(self):
        for barracks in self.structures(UnitTypeId.BARRACKS).ready.idle:
            if barracks.has_add_on:
                if barracks.add_on_tag in self.structures(UnitTypeId.BARRACKSTECHLAB).tags:
                    if self.can_afford(UnitTypeId.MARINE):
                        barracks.train(UnitTypeId.MARINE)
                elif barracks.add_on_tag in self.structures(UnitTypeId.BARRACKSREACTOR).tags:
                    for _ in range(2):
                        if self.can_afford(UnitTypeId.MARINE):
                            barracks.train(UnitTypeId.MARINE)
            else:
                if self.can_afford(UnitTypeId.MARINE):
                    barracks.train(UnitTypeId.MARINE)

    def train_marauders(self):
        for barracks in self.structures(UnitTypeId.BARRACKS).ready.idle:
            if barracks.has_add_on:
                if barracks.add_on_tag in self.structures(UnitTypeId.BARRACKSTECHLAB).tags:
                    if self.can_afford(UnitTypeId.MARAUDER):
                        barracks.train(UnitTypeId.MARAUDER)

    async def train_military_units(self):
        self.train_units_if_needed(UnitTypeId.SIEGETANK)
        self.train_units_if_needed(UnitTypeId.MEDIVAC)
        self.train_units_if_needed(UnitTypeId.RAVEN)
        self.train_units_if_needed(UnitTypeId.MARAUDER)
        self.train_units_if_needed(UnitTypeId.MARINE)

    def should_attack(self):
        # Get our military units
        military_units = self.units.filter(
            lambda unit: unit.type_id in {
                UnitTypeId.MARINE,
                UnitTypeId.MARAUDER,
                UnitTypeId.REAPER,
                UnitTypeId.SIEGETANK,
                UnitTypeId.SIEGETANKSIEGED,
                UnitTypeId.MEDIVAC,
                UnitTypeId.RAVEN
            }
        )
        
        # Define worker unit types
        worker_types = {
            UnitTypeId.SCV,
            UnitTypeId.PROBE,
            UnitTypeId.DRONE,
            UnitTypeId.MULE
        }
        
        # Filter out workers and structures from enemy units
        enemy_combat_units = self.enemy_units.filter(
            lambda unit: not unit.is_structure and unit.type_id not in worker_types
        )
        
        # Check for enemies near our bases first
        if self.townhalls:
            for base in self.townhalls:
                nearby_enemies = enemy_combat_units.filter(
                    lambda unit: unit.distance_to(base) < 30
                )
                if nearby_enemies:
                    # If we have a significant force near the threatened base, counter-attack
                    nearby_defenders = military_units.filter(
                        lambda unit: unit.distance_to(base) < 40
                    )
                    if len(nearby_defenders) > len(nearby_enemies) * 1.5:
                        # print(f"Counter-attacking near base with superior force!")
                        return True
                    return False  # Defend if we don't have superior numbers
        
        # Original attack conditions
        if self.get_military_supply() > 20:
            return True
            
        if self.supply_used > 180:
            # print(f"supply used is max, attacking")
            return True
        
        # Check for numerical advantage based on unit cost (minerals + gas)
        if len(enemy_combat_units) > 5:
            # Calculate total value of enemy units
            enemy_army_value = sum(
                sum(self.get_unit_mineral_and_gas_cost(unit.type_id))  # Sum of minerals and gas
                for unit in enemy_combat_units
            )
            
            # Only count our units that are close enough to the enemy (within 30 distance)
            if enemy_combat_units:
                enemy_center = enemy_combat_units.center
                nearby_military_units = military_units.filter(
                    lambda unit: unit.distance_to(enemy_center) < 30
                )
                
                # Calculate our nearby military value
                our_nearby_army_value = sum(
                    sum(self.get_unit_mineral_and_gas_cost(unit.type_id))  # Sum of minerals and gas
                    for unit in nearby_military_units
                )
                
                # Attack if we have a significant army value advantage
                advantage_ratio = 2.0
                if len(nearby_military_units) > 10:
                    advantage_ratio = 1.5
                elif len(nearby_military_units) > 15:
                    advantage_ratio = 1.3

                if our_nearby_army_value > enemy_army_value * advantage_ratio:
                    print(f"Army value advantage detected: {our_nearby_army_value} vs {enemy_army_value}, attacking")
                    return True
        
        return False

    def get_military_supply(self):
        military_supply = 0
        military_supply += self.units(UnitTypeId.MARINE).amount * 1
        military_supply += self.units(UnitTypeId.MARAUDER).amount * 2
        military_supply += self.units(UnitTypeId.REAPER).amount * 1
        military_supply += self.units(UnitTypeId.SIEGETANK).amount * 4  # Siege Tank costs 3 supply
        military_supply += self.units(UnitTypeId.SIEGETANKSIEGED).amount * 4  # Include sieged tanks
        return military_supply

    async def append_addons(self):
        """Manage add-ons for barracks, maintaining a 6:4 ratio of tech labs to reactors."""
        # Count current add-ons
        techlab_count = self.structures(UnitTypeId.BARRACKSTECHLAB).amount
        reactor_count = self.structures(UnitTypeId.BARRACKSREACTOR).amount
        total_addons = techlab_count + reactor_count
        
        for barracks in self.structures(UnitTypeId.BARRACKS).ready.idle:
            if not barracks.has_add_on:
                # Calculate desired ratio (6:4)
                desired_techlab_ratio = 0.6
                current_techlab_ratio = techlab_count / total_addons if total_addons > 0 else 0
                
                # If current techlab ratio is below 0.6, build techlab
                if current_techlab_ratio < desired_techlab_ratio:
                    await self.append_addon(UnitTypeId.BARRACKS, UnitTypeId.BARRACKSFLYING, UnitTypeId.BARRACKSTECHLAB)
                    techlab_count += 1
                # Otherwise build reactor
                else:
                    await self.append_addon(UnitTypeId.BARRACKS, UnitTypeId.BARRACKSFLYING, UnitTypeId.BARRACKSREACTOR)
                    reactor_count += 1
                total_addons += 1

        # Add tech lab to factory for tanks
        for factory in self.structures(UnitTypeId.FACTORY).ready.idle:
            if not factory.has_add_on:
                await self.append_addon(UnitTypeId.FACTORY, UnitTypeId.FACTORYFLYING, UnitTypeId.FACTORYTECHLAB)

        # Add tech lab to first starport for ravens, reactors to others
        starports = self.structures(UnitTypeId.STARPORT).ready.idle
        tech_lab_starports = self.structures(UnitTypeId.STARPORT).filter(
            lambda sp: sp.has_add_on and sp.add_on_tag in self.structures(UnitTypeId.STARPORTTECHLAB).tags
        )
        
        for starport in starports:
            if not starport.has_add_on:
                # Build tech lab if we don't have one yet
                if len(tech_lab_starports) < 1:
                    await self.append_addon(UnitTypeId.STARPORT, UnitTypeId.STARPORTFLYING, UnitTypeId.STARPORTTECHLAB)
                else:
                    await self.append_addon(UnitTypeId.STARPORT, UnitTypeId.STARPORTFLYING, UnitTypeId.STARPORTREACTOR)

    async def append_addon(self, building_type, building_flying_type, add_on_type):
        def points_to_build_addon(building_position: Point2) -> list[Point2]:
            addon_offset: Point2 = Point2((2.5, -0.5))
            addon_position: Point2 = building_position + addon_offset
            addon_points = [
                (addon_position + Point2((x - 0.5, y - 0.5))).rounded for x in range(0, 2) for y in range(0, 2)
            ]
            return addon_points

        for building in self.structures(building_type).ready.idle:
            if not building.has_add_on and self.can_afford(add_on_type):
                addon_points = points_to_build_addon(building.position)
                if all(
                    self.in_map_bounds(addon_point)
                    and self.in_placement_grid(addon_point)
                    and self.in_pathing_grid(addon_point)
                    for addon_point in addon_points
                ):
                    building.build(add_on_type)
                else:
                    building(AbilityId.LIFT)

        def land_positions(position: Point2) -> list[Point2]:
            land_positions = [(position + Point2((x, y))).rounded for x in range(-1, 2) for y in range(-1, 2)]
            return land_positions + points_to_build_addon(position)

        for building in self.structures(building_flying_type).idle:
            possible_land_positions_offset = sorted(
                (Point2((x, y)) for x in range(-10, 10) for y in range(-10, 10)),
                key=lambda point: point.x**2 + point.y**2,
            )
            offset_point: Point2 = Point2((-0.5, -0.5))
            possible_land_positions = (building.position.rounded + offset_point + p for p in possible_land_positions_offset)
            for target_land_position in possible_land_positions:
                land_and_addon_points: list[Point2] = land_positions(target_land_position)
                if all(
                    self.in_map_bounds(land_pos) and self.in_placement_grid(land_pos) and self.in_pathing_grid(land_pos)
                    for land_pos in land_and_addon_points
                ):
                    building(AbilityId.LAND, target_land_position)
                    break

    def should_expand_base(self):
        # Don't expand if we're at max bases
        if len(self.townhalls) > 12:
            return False
        
        if self.townhalls.ready.amount == 1 and self.already_pending(UnitTypeId.COMMANDCENTER) == 1:
            return False
        
        # Check if we're already expanding for equal or more than 2 bases
        if self.already_pending(UnitTypeId.COMMANDCENTER) >= 2:
            return False

        # Check if current bases are saturated (16 workers per base is optimal)
        for th in self.townhalls.ready:
            # Get nearby mineral fields
            mineral_fields = self.mineral_field.closer_than(10, th)
            
            # Skip this base if it's nearly mined out
            total_minerals = sum(mf.mineral_contents for mf in mineral_fields)
            if total_minerals < 2000:  # Skip bases with less than 2000 minerals remaining
                continue
            
            # Check worker saturation for viable bases
            if len(self.workers.closer_than(10, th)) < 16:
                return False  # Don't expand if current viable bases aren't almost fully utilized
            
        return True

    async def expand_base(self):
        # Don't expand if we can't afford it
        if not self.can_afford(UnitTypeId.COMMANDCENTER):
            return
        
        print(f"expanding base")
            
        # Get all possible expansion locations
        expansion_locations = self.expansion_locations_list
        
        # Filter out locations where we already have a base or one is being built
        existing_base_locations = {th.position for th in self.townhalls}  # Existing bases
        existing_base_locations.update(  # Add pending bases
            building.position for building in self.structures(UnitTypeId.COMMANDCENTER).not_ready
        )
        
        available_locations = [loc for loc in expansion_locations if loc not in existing_base_locations]
        
        if not available_locations:
            print(f"no available locations")
            return
        
        # Score each expansion location
        best_location = None
        best_score = -1
        
        for loc in available_locations:
            # Get mineral fields near this location
            nearby_minerals = self.mineral_field.closer_than(10, loc)
            mineral_value = sum(mf.mineral_contents for mf in nearby_minerals)
            
            # Calculate distance from our main base
            distance_to_main = loc.distance_to(self.start_location)
            
            # Calculate distance to enemy base
            distance_to_enemy = loc.distance_to(self.enemy_start_locations[0])
            
            # Calculate score based on minerals and safety
            # Prefer locations with more minerals and closer to our main
            # Penalize locations too close to enemy
            score = (mineral_value * 0.01  # Mineral value weight
                    - distance_to_main * 2  # Distance penalty
                    + distance_to_enemy * 1)  # Safety bonus
            
            # Additional safety check - don't expand too close to enemy
            if distance_to_enemy < 40:
                continue
            
            if score > best_score:
                best_score = score
                best_location = loc
        
        # Expand to the best location
        if best_location:
            print(f"expanding to {best_location}")
            await self.expand_now(location=best_location)
        else:
            print(f"no good location to expand to, use default")
            await self.expand_now()
                

    async def upgrade_army(self):
        # Only start upgrades when we have enough units
        if self.get_military_supply() < 30:
            return

        # Build Engineering Bays if we don't have them and can afford it
        if (len(self.structures(UnitTypeId.ENGINEERINGBAY)) + self.already_pending(UnitTypeId.ENGINEERINGBAY) < 2 and 
            self.can_afford(UnitTypeId.ENGINEERINGBAY)):
            await self.build_structure_if_needed(UnitTypeId.ENGINEERINGBAY)
            return

        # Build Factory if we don't have one (required for Armory)
        if (self.structures(UnitTypeId.BARRACKS).ready and
            not self.structures(UnitTypeId.FACTORY) and
            not self.already_pending(UnitTypeId.FACTORY) and
            self.can_afford(UnitTypeId.FACTORY)):
            await self.build_structure_if_needed(UnitTypeId.FACTORY)
            return

        # Build Armory for level 2 and 3 upgrades
        if (self.structures(UnitTypeId.ENGINEERINGBAY).ready and 
            self.structures(UnitTypeId.FACTORY).ready and  # Factory must be ready
            not self.structures(UnitTypeId.ARMORY) and 
            not self.already_pending(UnitTypeId.ARMORY) and 
            self.can_afford(UnitTypeId.ARMORY)):
            
            # Find placement for armory (no addon needed)
            if self.townhalls:
                pos = await self.find_placement(
                    UnitTypeId.ARMORY,
                    near_position=self.townhalls.first.position,
                    min_distance=5,
                    max_distance=20,
                    addon_space=False
                )
                
                if pos:
                    await self.build(UnitTypeId.ARMORY, near=pos)
            return

        # Get Engineering Bays
        ebays = self.structures(UnitTypeId.ENGINEERINGBAY).ready
        if not ebays:
            return

        has_armory = self.structures(UnitTypeId.ARMORY).ready.exists

        # Use first ebay for weapons
        if len(ebays) >= 1:
            if not self.already_pending_upgrade(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1):
                ebays[0].research(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)
            elif has_armory and not self.already_pending_upgrade(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2):
                ebays[0].research(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2)
            elif has_armory and not self.already_pending_upgrade(UpgradeId.TERRANINFANTRYWEAPONSLEVEL3):
                ebays[0].research(UpgradeId.TERRANINFANTRYWEAPONSLEVEL3)

        # Use second ebay for armor
        if len(ebays) >= 2:
            if not self.already_pending_upgrade(UpgradeId.TERRANINFANTRYARMORSLEVEL1):
                ebays[1].research(UpgradeId.TERRANINFANTRYARMORSLEVEL1)
            elif has_armory and not self.already_pending_upgrade(UpgradeId.TERRANINFANTRYARMORSLEVEL2):
                ebays[1].research(UpgradeId.TERRANINFANTRYARMORSLEVEL2)
            elif has_armory and not self.already_pending_upgrade(UpgradeId.TERRANINFANTRYARMORSLEVEL3):
                ebays[1].research(UpgradeId.TERRANINFANTRYARMORSLEVEL3)

    async def find_placement(self, building_type, near_position, min_distance=7, max_distance=30, addon_space=False, placement_step=2):
        """
        Find a suitable placement for a building that ensures proper spacing and unit pathing.
        
        Args:
            building_type: The type of building to place
            near_position: The reference position to build near
            min_distance: Minimum distance from other buildings
            max_distance: Maximum distance from reference position
            addon_space: Whether to reserve space for an addon
            placement_step: Step size for the placement grid
            
        Returns:
            A Point2 position or None if no valid position found
        """
        # Increase min_distance to ensure better spacing between buildings
        # Original was 5-6, now using 7 by default (allows tanks to move through)
        
        # Create a list of potential positions in a spiral pattern
        positions = []
        for distance in range(7, max_distance, placement_step):
            for angle in range(0, 360, 20):  # Check every 20 degrees for more options
                radians = math.radians(angle)
                x = near_position.x + (distance * math.cos(radians))
                y = near_position.y + (distance * math.sin(radians))
                positions.append(Point2((x, y)))
        
        # Shuffle positions for more varied building placement
        random.shuffle(positions)
        
        # Get existing buildings
        existing_buildings = self.structures.not_flying
        
        # For buildings that need addon space, we need extra checking
        if addon_space:
            # Check these positions for both building and addon placement
            for pos in positions:
                # First check if we can place the building here
                if await self.can_place(building_type, pos):
                    # Check distance to other buildings (needs to be larger for better pathing)
                    if all(building.distance_to(pos) > min_distance for building in existing_buildings):
                        # Then check if we can place an addon (use supply depot as a proxy for addon size)
                        addon_pos = Point2((pos.x + 2.5, pos.y - 0.5))
                        if await self.can_place(UnitTypeId.SUPPLYDEPOT, addon_pos):
                            # Final check: verify pathing in the surrounding area
                            # Create a grid of points around the building to check for pathing
                            path_check_points = []
                            for x_offset in [-3, 0, 3]:
                                for y_offset in [-3, 0, 3]:
                                    if x_offset == 0 and y_offset == 0:
                                        continue  # Skip the center point (where building will be)
                                    path_check_points.append(Point2((pos.x + x_offset, pos.y + y_offset)))
                            
                            # Check if most of these points are in the pathing grid
                            valid_path_points = sum(1 for p in path_check_points if self.in_pathing_grid(p))
                            if valid_path_points >= 6:  # At least 6 of 8 points should be pathable
                                return pos
        else:
            # Regular building (no addon)
            for pos in positions:
                if await self.can_place(building_type, pos):
                    # Check distance to other buildings (needs to be larger for better pathing)
                    if all(building.distance_to(pos) > min_distance for building in existing_buildings):
                        # Verify pathing in the surrounding area
                        path_check_points = []
                        for x_offset in [-3, 0, 3]:
                            for y_offset in [-3, 0, 3]:
                                if x_offset == 0 and y_offset == 0:
                                    continue  # Skip the center
                                path_check_points.append(Point2((pos.x + x_offset, pos.y + y_offset)))
                        
                        valid_path_points = sum(1 for p in path_check_points if self.in_pathing_grid(p))
                        if valid_path_points >= 6:
                            print(f"Found good placement for {building_type} at {pos}")
                            return pos
        
        # Fall back to standard placement but still with increased min_distance
        return await super().find_placement(building_type, near=near_position, placement_step=placement_step)

    def get_unit_mineral_and_gas_cost(self, unit_type_id: UnitTypeId) -> tuple[int, int]:
        """
        Get the mineral and gas cost of a unit type.
        Uses game data when possible, falls back to a comprehensive dictionary.
        
        Args:
            unit_type_id: The unit type ID to get costs for
            
        Returns:
            Tuple of (mineral_cost, gas_cost)
        """
        # Try to get from game data first
        try:
            unit_data = self._game_data.units[unit_type_id.value]
            if hasattr(unit_data, 'cost'):
                return (unit_data.cost.minerals, unit_data.cost.vespene)
        except (KeyError, AttributeError):
            pass
        
        # Comprehensive dictionary of unit costs (mineral, gas)
        unit_costs = {
            # Terran
            UnitTypeId.SCV: (50, 0),
            UnitTypeId.MARINE: (50, 0),
            UnitTypeId.MARAUDER: (100, 25),
            UnitTypeId.REAPER: (50, 50),
            UnitTypeId.GHOST: (150, 125),
            UnitTypeId.HELLION: (100, 0),
            UnitTypeId.HELLIONTANK: (100, 0),
            UnitTypeId.SIEGETANK: (150, 125),
            UnitTypeId.SIEGETANKSIEGED: (150, 125),
            UnitTypeId.CYCLONE: (150, 100),
            UnitTypeId.WIDOWMINE: (75, 25),
            UnitTypeId.WIDOWMINEBURROWED: (75, 25),
            UnitTypeId.THOR: (300, 200),
            UnitTypeId.THORAP: (300, 200),
            UnitTypeId.VIKINGFIGHTER: (150, 75),
            UnitTypeId.VIKINGASSAULT: (150, 75),
            UnitTypeId.MEDIVAC: (100, 100),
            UnitTypeId.LIBERATOR: (150, 150),
            UnitTypeId.LIBERATORAG: (150, 150),
            UnitTypeId.RAVEN: (100, 200),
            UnitTypeId.BANSHEE: (150, 100),
            UnitTypeId.BATTLECRUISER: (400, 300),
            
            # Protoss
            UnitTypeId.PROBE: (50, 0),
            UnitTypeId.ZEALOT: (100, 0),
            UnitTypeId.STALKER: (125, 50),
            UnitTypeId.SENTRY: (50, 100),
            UnitTypeId.ADEPT: (100, 25),
            UnitTypeId.HIGHTEMPLAR: (50, 150),
            UnitTypeId.DARKTEMPLAR: (125, 125),
            UnitTypeId.IMMORTAL: (275, 100),
            UnitTypeId.COLOSSUS: (300, 200),
            UnitTypeId.DISRUPTOR: (150, 150),
            UnitTypeId.ARCHON: (100, 300),  # Approximation (2 HTs)
            UnitTypeId.OBSERVER: (25, 75),
            UnitTypeId.WARPPRISM: (200, 0),
            UnitTypeId.PHOENIX: (150, 100),
            UnitTypeId.VOIDRAY: (250, 150),
            UnitTypeId.ORACLE: (150, 150),
            UnitTypeId.CARRIER: (350, 250),
            UnitTypeId.TEMPEST: (250, 175),
            UnitTypeId.MOTHERSHIP: (400, 400),
            
            # Zerg
            UnitTypeId.DRONE: (50, 0),
            UnitTypeId.ZERGLING: (25, 0),
            UnitTypeId.BANELING: (25, 25),  # Plus zergling cost
            UnitTypeId.ROACH: (75, 25),
            UnitTypeId.RAVAGER: (75, 75),  # Plus roach cost
            UnitTypeId.HYDRALISK: (100, 50),
            UnitTypeId.LURKER: (50, 100),  # Plus hydra cost
            UnitTypeId.INFESTOR: (100, 150),
            UnitTypeId.SWARMHOSTMP: (100, 75),
            UnitTypeId.ULTRALISK: (300, 200),
            UnitTypeId.OVERLORD: (100, 0),
            UnitTypeId.OVERSEER: (50, 50),  # Plus overlord cost
            UnitTypeId.MUTALISK: (100, 100),
            UnitTypeId.CORRUPTOR: (150, 100),
            UnitTypeId.BROODLORD: (150, 150),  # Plus corruptor cost
            UnitTypeId.VIPER: (100, 200),
        }
        
        # Return from dictionary if available, otherwise default to (100, 25)
        return unit_costs.get(unit_type_id, (100, 25))

    def get_total_structure_count(self, unit_type):
        """
        Count the total number of a unit type, including ready, flying, and pending structures.
        
        Args:
            unit_type: The UnitTypeId to count
            
        Returns:
            int: Total count of ready, flying, and pending units/structures
        """
        # Count ready structures
        ready_count = self.structures(unit_type).ready.amount
        
        # Count flying buildings (Terran specific)
        flying_count = 0
        if unit_type in {UnitTypeId.COMMANDCENTER, UnitTypeId.ORBITALCOMMAND, UnitTypeId.PLANETARYFORTRESS}:
            flying_count = self.structures(UnitTypeId.COMMANDCENTERFLYING).amount
        elif unit_type == UnitTypeId.BARRACKS:
            flying_count = self.structures(UnitTypeId.BARRACKSFLYING).amount
        elif unit_type == UnitTypeId.FACTORY:
            flying_count = self.structures(UnitTypeId.FACTORYFLYING).amount
        elif unit_type == UnitTypeId.STARPORT:
            flying_count = self.structures(UnitTypeId.STARPORTFLYING).amount
        
        # Count pending structures
        pending_count = self.already_pending(unit_type)
        
        # Calculate total
        total_count = ready_count + flying_count + pending_count
        
        return total_count

def main():
    bot = HanBot()
#    maps_pool = ["CatalystLE"]
    maps_pool = ["AcropolisAIE"]
    run_game(
        maps.get(maps_pool[0]),
        [
            Bot(Race.Terran, bot),
            Computer(Race.Zerg, Difficulty.CheatInsane)
#            Computer(Race.Protoss, Difficulty.CheatInsane)
        ],
        realtime=False
    )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())