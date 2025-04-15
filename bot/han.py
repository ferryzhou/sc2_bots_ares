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

class HanBot(BotAI):
    async def on_step(self, iteration):
        await self.manage_army()
        # Basic economy management
        await self.distribute_workers()
        await self.build_supply_depot_if_needed()
        await self.manage_mules()
        await self.train_workers()

        # print(f"we have {len(self.townhalls)} bases, {self.townhalls.ready.amount} ready, {self.already_pending(UnitTypeId.COMMANDCENTER)} pending")   

        if self.should_expand_base():                   
            if self.can_afford(UnitTypeId.COMMANDCENTER):
                await self.expand_base()
            elif self.time >= 480: # After first 8 minutes
                print(f"can't afford to expand, stop production in late game")
                return
        # Additional game management
        if iteration % 10 == 0:  # Every 10 iterations
            print(f"iteration {iteration}")
            await self.manage_production()

    async def manage_production(self):
        # print(f"manage_production")
        await self.build_gas_if_needed()
        await self.build_factory_if_needed()
        await self.build_barracks_if_needed()
        await self.build_starport_if_needed()
        await self.build_engineering_bay_if_needed()
        await self.append_addons()
        await self.upgrade_army()
        await self.train_military_units()

    async def manage_army(self):
        # Get all military units
        military_units = self.units(UnitTypeId.MARINE) | self.units(UnitTypeId.MARAUDER)
        tanks = self.units(UnitTypeId.SIEGETANK) | self.units(UnitTypeId.SIEGETANKSIEGED)
        medivacs = self.units(UnitTypeId.MEDIVAC)
        ravens = self.units(UnitTypeId.RAVEN)
        
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

        print(f"attacking")
        await self.execute_attack(military_units, tanks)

    def detected_cheese(self):
        if self.time >= 300: # First 5 minutes
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
        """Execute attack logic with enhanced unit micro."""
        enemy_units = self.enemy_units
        enemy_structures = self.enemy_structures
        enemy_start = self.enemy_start_locations[0]

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
        
        # Enhanced unit micro for attacking units
        for unit in military_units:
            # Find nearby enemies (excluding overlords)
            nearby_enemies = enemy_units.filter(
                lambda enemy: enemy.distance_to(unit) < 50
            )
            
            if nearby_enemies:
                # Get closest enemy
                closest_enemy = nearby_enemies.closest_to(unit)
                
                # Enhanced micro based on unit health and enemy type
                if unit.ground_range > 1:  # Ranged unit micro
                    if unit.weapon_cooldown > 0:  # If we can't shoot, move away
                        retreat_pos = unit.position.towards(closest_enemy.position, -2)
                        unit.move(retreat_pos)
                    else:  # If we can shoot, attack
                        unit.attack(closest_enemy)
                else:  # Melee units or other cases
                    unit.attack(closest_enemy)
            elif enemy_structures:
                # Attack nearest structure if no units nearby
                closest_structure = enemy_structures.closest_to(unit)
                unit.attack(closest_structure)
            else:
                unit.attack(enemy_start)
        
        # Enhanced tank micro for attacking
        for tank in tanks:
            target = enemy_start
            if enemy_units:
                target = enemy_units.closest_to(tank)
            elif enemy_structures:
                target = enemy_structures.closest_to(tank)
            
            await self.manage_attacking_tank(tank, target)

        await self.manage_attacking_ravens(self.units(UnitTypeId.RAVEN), enemy_units)

    async def manage_attacking_tank(self, tank, target):
        """Manage tank positioning and siege mode during attacks."""
        enemy_distance = target.distance_to(tank)
        
        if tank.type_id == UnitTypeId.SIEGETANK:
            if enemy_distance < 13:  # Optimal siege range
                tank(AbilityId.SIEGEMODE_SIEGEMODE)
            else:
                # Move closer while avoiding getting too close
                desired_position = target.position.towards(tank.position, 12)
                tank.move(desired_position)
        elif tank.type_id == UnitTypeId.SIEGETANKSIEGED:
            if enemy_distance > 15:  # Enemy moved out of range
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
                        print(f"Raven {raven.tag} dropping turret during attack")
    

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
        if self.townhalls.ready.amount == 1:
            return 1
        if self.townhalls.ready.amount == 2:
            return 3
        return self.townhalls.ready.amount * 1.2 + 2

    async def build_gas_if_needed(self):
        if self.get_total_structure_count(UnitTypeId.BARRACKS) == 0:
            return

        total_refineries = self.get_total_structure_count(UnitTypeId.REFINERY)

        if total_refineries >= self.get_max_refineries():
            return

        for th in self.townhalls.ready:
            vgs = self.vespene_geyser.closer_than(10, th)
            for vg in vgs:
                if await self.can_place_single(UnitTypeId.REFINERY, vg.position) and self.can_afford(UnitTypeId.REFINERY):
                    workers = self.workers.gathering
                    if workers:
                        worker = workers.closest_to(vg)
                        worker.build_gas(vg)
                        return

    def get_max_barracks(self):
        if self.townhalls.ready.amount == 1:
            return 2
        return min(self.workers.amount // 6, 12)

    async def build_barracks_if_needed(self):
        if not self.townhalls:
            return

        if not self.townhalls.ready:
            return
            
        if not self.can_afford(UnitTypeId.BARRACKS):
            return
            
        total_barracks = self.get_total_structure_count(UnitTypeId.BARRACKS)
        
        if total_barracks >= self.get_max_barracks():
            return
        
        # Get main base and its position
        cc = self.townhalls.first
        base_pos = cc.position
        
        # Try primary placement method
        pos = await self.find_placement(
            UnitTypeId.BARRACKS,
            near_position=base_pos,
            min_distance=6,
            max_distance=25,
            addon_space=True
        )
        
        if pos:
            print(f"Building barracks at position {pos}")
            await self.build(UnitTypeId.BARRACKS, near=pos)
        else:
            # Fallback method 1: Try direct placement
            print("Fallback: Using direct placement for barracks")
            potential_positions = [
                base_pos.towards(self.game_info.map_center, 8),
                base_pos.towards(self.game_info.map_center, 12),
                base_pos.towards(self.game_info.map_center, 16)
            ]
            
            for fallback_pos in potential_positions:
                if await self.can_place(UnitTypeId.BARRACKS, fallback_pos):
                    await self.build(UnitTypeId.BARRACKS, near=fallback_pos)
                    return
            
            # Fallback method 2: Just try the standard build method near base
            await self.build(UnitTypeId.BARRACKS, near=base_pos)

    async def build_factory_if_needed(self):
        # Need barracks before factory
        if not self.structures(UnitTypeId.BARRACKS).ready:
            return
        
        if not self.can_afford(UnitTypeId.FACTORY):
            return
        
        if not self.townhalls:
            return
        
        # Get current factory count (including flying factories)
        total_factories = self.get_total_structure_count(UnitTypeId.FACTORY)

        # Always build first factory when we have enough military units
        if total_factories == 0 and self.get_military_supply() >= 10:
            # Find placement for factory with addon space
            pos = await self.find_placement(
                UnitTypeId.FACTORY,
                near_position=self.townhalls.first.position,
                min_distance=6,
                max_distance=25,
                addon_space=True
            )
            
            if pos:
                print(f"Building factory at position {pos}")
                await self.build(UnitTypeId.FACTORY, near=pos)
            else:
                print("Fallback: Using direct placement for factory")
                # Fallback: build near any barracks
                barracks = self.structures(UnitTypeId.BARRACKS).ready
                if barracks:
                    await self.build(UnitTypeId.FACTORY, near=barracks.random.position.towards(self.game_info.map_center, 7))
                else:
                    await self.build(UnitTypeId.FACTORY, near=self.townhalls.first)
            return

        # Only build second factory when we have a large ground army
        ground_units = self.units(UnitTypeId.MARINE).amount + self.units(UnitTypeId.MARAUDER).amount
        if total_factories == 1 and ground_units >= 30:
            pos = await self.find_placement(
                UnitTypeId.FACTORY,
                near_position=self.townhalls.first.position,
                min_distance=6,
                max_distance=25,
                addon_space=True
            )
            
            if pos:
                await self.build(UnitTypeId.FACTORY, near=pos)
            else:
                # Fallback: build near any barracks
                barracks = self.structures(UnitTypeId.BARRACKS).ready
                if barracks:
                    await self.build(UnitTypeId.FACTORY, near=barracks.random.position.towards(self.game_info.map_center, 7))

    async def build_starport_if_needed(self):
        # Need at least one factory before starport
        if not self.structures(UnitTypeId.FACTORY).ready:
            return
    
        if not self.can_afford(UnitTypeId.STARPORT):
            return
        
        if not self.townhalls:
            return

        # Check if we already have starports or one is in progress (including flying)
        total_starports = self.get_total_structure_count(UnitTypeId.STARPORT)
        
        if total_starports >= 2:
            return

        # Find placement for starport with addon space
        pos = await self.find_placement(
            UnitTypeId.STARPORT,
            near_position=self.townhalls.first.position,
            min_distance=6,
            max_distance=25,
            addon_space=True
        )
        
        if pos:
            print(f"Building starport at position {pos}")
            await self.build(UnitTypeId.STARPORT, near=pos)
        else:
            print("Fallback: Using direct placement for starport")
            # Fallback: build near factory or barracks
            if self.structures(UnitTypeId.FACTORY).ready:
                await self.build(UnitTypeId.STARPORT, near=self.structures(UnitTypeId.FACTORY).ready.random.position)
            elif self.structures(UnitTypeId.BARRACKS).ready:
                await self.build(UnitTypeId.STARPORT, near=self.structures(UnitTypeId.BARRACKS).ready.random.position)
            else:
                await self.build(UnitTypeId.STARPORT, near=self.townhalls.first)

    async def build_engineering_bay_if_needed(self):
        # Only start upgrades when we have enough units
        if self.get_military_supply() < 30:
            return
        
        if not self.townhalls:
            return
        
        if self.get_total_structure_count(UnitTypeId.ENGINEERINGBAY) >= 2:
            return
        
        if not self.can_afford(UnitTypeId.ENGINEERINGBAY):
            return

        # Find placement for engineering bay (no addon needed)
        pos = await self.find_placement(
            UnitTypeId.ENGINEERINGBAY,
            near_position=self.townhalls.first.position,
            min_distance=5,
            max_distance=20,
            addon_space=False
        )
        
        if pos:
            print(f"Building engineering bay at position {pos}")
            await self.build(UnitTypeId.ENGINEERINGBAY, near=pos)
        else:
            print("Fallback: Using direct placement for engineering bay")
            # Fallback method for engineering bay
            await self.build(UnitTypeId.ENGINEERINGBAY, near=self.townhalls.first.position.towards(self.game_info.map_center, 8))

    def build_tanks_if_needed(self):
        # Build tanks if we have enough military units and a factory with tech lab
        if self.get_military_supply() >= 10:
            for factory in self.structures(UnitTypeId.FACTORY).ready.idle:
                if factory.has_add_on:
                    if factory.add_on_tag in self.structures(UnitTypeId.FACTORYTECHLAB).tags:
                        if self.can_afford(UnitTypeId.SIEGETANK) and self.supply_left > 4:
                            factory.train(UnitTypeId.SIEGETANK)

    def build_medivacs_if_needed(self):
        # Build medivacs based on ground unit count
        ground_units = self.units(UnitTypeId.MARINE).amount + self.units(UnitTypeId.MARAUDER).amount
        desired_medivacs = ground_units // 8  # One medivac for every 8 ground units
        current_medivacs = self.units(UnitTypeId.MEDIVAC).amount
        
        if current_medivacs < desired_medivacs:
            for starport in self.structures(UnitTypeId.STARPORT).ready.idle:
                if self.can_afford(UnitTypeId.MEDIVAC) and self.supply_left > 2:
                    starport.train(UnitTypeId.MEDIVAC)

    def build_ravens_if_needed(self):
        # Build Ravens (up to 2)
        current_ravens = self.units(UnitTypeId.RAVEN).amount
        desired_ravens = 2
        
        if current_ravens < desired_ravens:
            for starport in self.structures(UnitTypeId.STARPORT).ready.idle:
                if starport.has_add_on:
                    if starport.add_on_tag in self.structures(UnitTypeId.STARPORTTECHLAB).tags:
                        if self.can_afford(UnitTypeId.RAVEN) and self.supply_left > 2:
                            starport.train(UnitTypeId.RAVEN)

    def build_marines_marauders_if_needed(self):
        marine_count = self.units(UnitTypeId.MARINE).amount + self.already_pending(UnitTypeId.MARINE)
        marauder_count = self.units(UnitTypeId.MARAUDER).amount + self.already_pending(UnitTypeId.MARAUDER)

        should_train_marauders = marauder_count < marine_count

        for barracks in self.structures(UnitTypeId.BARRACKS).ready.idle:
            if barracks.has_add_on:
                if barracks.add_on_tag in self.structures(UnitTypeId.BARRACKSTECHLAB).tags:
                    if should_train_marauders:
                        if self.can_afford(UnitTypeId.MARAUDER) and self.supply_left > 2:
                            barracks.train(UnitTypeId.MARAUDER)
                    else:
                        if self.can_afford(UnitTypeId.MARINE) and self.supply_left > 1:
                            barracks.train(UnitTypeId.MARINE)
                elif barracks.add_on_tag in self.structures(UnitTypeId.BARRACKSREACTOR).tags:
                    for _ in range(2):
                        if self.can_afford(UnitTypeId.MARINE) and self.supply_left > 1:
                            barracks.train(UnitTypeId.MARINE)
            else:
                if self.can_afford(UnitTypeId.MARINE) and self.supply_left > 1:
                    barracks.train(UnitTypeId.MARINE)

    async def train_military_units(self):
        self.build_tanks_if_needed()
        self.build_medivacs_if_needed()
        self.build_ravens_if_needed()
        self.build_marines_marauders_if_needed()

    async def train_workers(self):
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
        
        # Check if we're already expanding for more than 2 bases
        if self.already_pending(UnitTypeId.COMMANDCENTER) > 2:
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
            await self.build_engineering_bay_if_needed()
            return

        # Build Factory if we don't have one (required for Armory)
        if (self.structures(UnitTypeId.BARRACKS).ready and
            not self.structures(UnitTypeId.FACTORY) and
            not self.already_pending(UnitTypeId.FACTORY) and
            self.can_afford(UnitTypeId.FACTORY)):
            await self.build_factory_if_needed()
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

    async def manage_mules(self):
        # Transform Command Center to Orbital Command if possible
        for cc in self.structures(UnitTypeId.COMMANDCENTER).ready.idle:
            if self.can_afford(UnitTypeId.ORBITALCOMMAND):
                cc(AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND)

        """Manage MULE production and optimal mineral mining."""
        # Check for Orbital Commands
        for oc in self.structures(UnitTypeId.ORBITALCOMMAND).ready:
            # Only call down MULE if we have enough energy
            if oc.energy >= 50:
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
#            Computer(Race.Protoss, Difficulty.CheatVision)
        ],
        realtime=False
    )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())