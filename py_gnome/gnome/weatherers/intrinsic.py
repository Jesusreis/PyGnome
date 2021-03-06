'''
For now just define a FayGravityInertial class here
State is not persisted yet - we just have a default object that gets
attached to Evaporation
'''
import os
import numpy as np

from gnome.basic_types import oil_status
from gnome.array_types import (density,
                               viscosity,
                               mass_components,
                               init_volume,
                               init_area,
                               relative_bouyancy,
                               area,
                               mass,
                               frac_coverage,
                               thickness,
                               frac_water,
                               frac_lost,
                               age,
                               init_mass)
from gnome import AddLogger, constants


class FayGravityViscous(object):
    def __init__(self):
        self.spreading_const = (1.53, 1.21)
        self.thickness_limit = .0001

    def init_area(self,
                  water_viscosity,
                  init_volume,
                  relative_bouyancy):
        '''
        Initial area is computed for each LE only once. This takes scalars
        inputs since water_viscosity, init_volume and relative_bouyancy for a
        bunch of LEs released together will be the same.

        :param water_viscosity: viscosity of water
        :type water_viscosity: float
        :param init_volume: total initial volume of all LEs released together
        :type init_volume: float
        :param relative_bouyancy: relative bouyance of oil wrt water:
            (rho_water - rho_oil)/rho_water where rho defines density
        :type relative_bouyancy: float

        Equation:
        A0 = np.pi*(k2**4/k1**2)*(((n_LE*V0)**5*g*dbuoy)/(nu_h2o**2))**(1./6.)
        '''
        self._check_relative_bouyancy(relative_bouyancy)
        out = (np.pi*(self.spreading_const[1]**4/self.spreading_const[0]**2)
               * (((init_volume)**5*constants.gravity*relative_bouyancy) /
                  (water_viscosity**2))**(1./6.))

        return out

    def _check_relative_bouyancy(self, rel_bouy):
        '''
        For now just raise an error if any relative_bouyancy is < 0. These
        particles will sink, ask how we want to deal with them. They should
        be removed or we should only look at floating particles when computing
        area?
        '''
        if np.any(rel_bouy < 0):
            raise ValueError("Found particles with relative_bouyancy < 0. "
                             "Area does not handle this case at present.")

    def update_area(self,
                    water_viscosity,
                    init_area,
                    init_volume,
                    relative_bouyancy,
                    age,
                    thickness,
                    area,   # update only if thickness > thickness_lim
                    frac_coverage=None,
                    out=None):
        '''
        Update area and stuff it in out array. This takes numpy arrays
        as input for init_volume, relative_bouyancy and age. Each
        element of the array is the property for an LE - array should be the
        same shape.

        Since this is for updating area, it assumes age > 0 for all elements.
        It is used inside IntrinsicProps and invoked for particles with age > 0

        It only updates the area for particles with thickness > xxx
        Since the frac_coverage should only be applied to particles which are
        updated, let's apply this in here.

        todo: unsure if thickness check should be here or outside this object.
        Since thickness limit is here, leave it for now, but maybe
        eventually move thickness_limit to OilProps/make it property of
        substance - say 'max_spreading_thickness', then move thickness check
        and frac_coverage back to IntrinsicProps
        '''
        self._check_relative_bouyancy(relative_bouyancy)
        if np.any(age == 0):
            raise ValueError('for new particles use init_area - age '
                             'must be > 0')

        if out is None:
            out = np.zeros_like(init_volume, dtype=np.float64)

        # ADIOS 2 used 0.1 mm as a minimum average spillet thickness for crude
        # oil and heavy refined products and 0.01 mm for lighter refined
        # products. Use 0.1mm for now
        out[:] = area
        mask = thickness > self.thickness_limit  # units of meters
        if np.any(mask):
            out[mask] = init_area[mask]
            dFay = (self.spreading_const[1]**2./16. *
                    (constants.gravity*relative_bouyancy[mask] *
                     init_volume[mask]**2 /
                     np.sqrt(water_viscosity*age[mask])))
            dEddy = 0.033*age[mask]**(4./25)
            out[mask] += (dFay + dEddy) * age[mask]

            # apply fraction coverage here so particles less than min thickness
            # are not changed
            if frac_coverage is not None:
                out[mask] *= frac_coverage[mask]

        return out


class IntrinsicProps(AddLogger):
    '''
    Updates intrinsic properties of Oil
    Doesn't have an id like other gnome objects. It isn't exposed to
    application since Model will automatically instantiate if there
    are any Weathering objects defined

    Use this to manage data_arrays associated with weathering that are not
    defined in Weatherers. This is inplace of defining initializers for every
    single array, let IntrinsicProps set/initialize/update these arrays.
    '''
    def __init__(self,
                 water,
                 spreading=FayGravityViscous()):
        self.water = water
        self.spreading = spreading
        self.array_types = {'density': density,
                            'viscosity': viscosity,
                            'mass_components': mass_components,
                            'mass': mass,
                            # init volume of all particles released together
                            'init_volume': init_volume,
                            'init_mass': init_mass,
                            'frac_water': frac_water,
                            'frac_lost': frac_lost,
                            'area': area,     # area no longer needs init_volume since
                            'init_area': init_area,
                            'relative_bouyancy': relative_bouyancy,
                            'frac_coverage': frac_coverage,
                            'thickness': thickness,
                            'age': age}
        # following used to update viscosity
        self.visc_curvfit_param = 1.5e3     # units are sec^0.5 / m
        self.visc_f_ref = 0.84

    def initialize(self, sc):
        '''
        1. initialize standard keys:
        avg_density, floating, amount_released, avg_viscosity to 0.0
        2. set init_density for all ElementType objects in each Spill
        '''
        # nothing released yet - set everything to 0.0
        for key in ('avg_density', 'floating', 'amount_released',
                    'avg_viscosity'):
            sc.weathering_data[key] = 0.0

    def update(self, num_new_released, sc):
        '''
        Uses 'substance' properties together with 'water' properties to update
        'density', 'init_volume', etc
        The 'init_volume' is not updated at each step; however, it depends on
        the 'density' which must be set/updated first and this depends on
        water object. So it was easiest to initialize the 'init_volume' for
        newly released particles here.
        '''
        if len(sc) > 0:
            self._update_intrinsic_props(sc)
            self._update_weathering_data(num_new_released, sc)

    def _update_weathering_data(self, new_LEs, sc):
        '''
        intrinsic LE properties not set by any weatherer so let SpillContainer
        set these - will user be able to use select weatherers? Currently,
        evaporation defines 'density' data array
        '''
        mask = sc['status_codes'] == oil_status.in_water
        # update avg_density from density array
        # wasted cycles at present since all values in density for given
        # timestep should be the same, but that will likely change
        # todo: test weighted average
        sc.weathering_data['avg_density'] = \
            np.sum(sc['mass']/np.sum(sc['mass']) * sc['density'])
        sc.weathering_data['avg_viscosity'] = \
            np.sum(sc['mass']/sc['mass'].sum() * sc['viscosity'])
        sc.weathering_data['floating'] = sc['mass'][mask].sum()

        if new_LEs > 0:
            amount_released = np.sum(sc['mass'][-new_LEs:])
            if 'amount_released' in sc.weathering_data:
                sc.weathering_data['amount_released'] += amount_released
            else:
                sc.weathering_data['amount_released'] = amount_released

    def _update_intrinsic_props(self, sc):
        '''
        - initialize 'density', 'viscosity', and other optional arrays for
        newly released particles.
        - update intrinsic properties like 'density', 'viscosity' and optional
        arrays for previously released particles
        '''
        arrays = self.array_types.keys()

        for substance, data in sc.itersubstancedata(arrays):
            'update properties only if elements are released'
            if len(data['density']) == 0:
                continue

            # could also use 'age' but better to use an uninitialized var since
            # we might end up changing 'age' to something with less than a
            # time_step resolution
            new_LEs_mask = data['density'] == 0
            if sum(new_LEs_mask) > 0:
                self._init_new_particles(new_LEs_mask, data, substance)
            if sum(~new_LEs_mask) > 0:
                self._update_old_particles(~new_LEs_mask, data, substance)

        sc.update_from_substancedata(arrays)

    def _init_new_particles(self, mask, data, substance):
        '''
        initialize new particles released together in a given timestep

        :param mask: mask gives only the new LEs in data arrays
        :type mask: numpy bool array
        :param data: dict containing numpy arrays
        :param substance: OilProps object defining the substance spilled
        '''
        water_temp = self.water.get('temperature', 'K')
        data['density'][mask] = substance.get_density(water_temp)

        # initialize mass_components - assume 'mass' is correctly set
        data['mass_components'][mask, :len(substance.mass_fraction)] = \
            (np.asarray(substance.mass_fraction, dtype=np.float64) *
             (data['mass'][mask].reshape(len(data['mass'][mask]), -1)))

        data['init_mass'][mask] = data['mass'][mask]

        if substance.get_viscosity(water_temp) is not None:
            'make sure we do not add NaN values'
            data['viscosity'][mask] = \
                substance.get_viscosity(water_temp)

        '''
        Sets relative_bouyancy, init_volume, init_area, thickness all of
        which are required when computing the 'area' of each LE
        '''
        data['relative_bouyancy'][mask] = \
            self._set_relative_bouyancy(data['density'][mask])

        # Cannot change the init_area in place since the following:
        #    sc['init_area'][-new_LEs:][in_spill]
        # is an advanced indexing operation that makes a copy anyway
        # Also, init_volume is same for all these new LEs so just provide
        # a scalar value
        data['init_volume'][mask] = np.sum(data['init_mass'][mask] /
                                           data['density'][mask], 0)
        data['init_area'][mask] = \
            self.spreading.init_area(self.water.get('kinematic_viscosity',
                                                    'square meter per second'),
                                     data['init_volume'][mask][0],
                                     data['relative_bouyancy'][mask][0])
        data['area'][mask] = data['init_area'][mask]
        data['thickness'][mask] = data['init_volume'][mask]/data['area'][mask]

    def _update_old_particles(self, mask, data, substance):
        '''
        update density, area
        '''
        # update density/viscosity/relative_bouyance/area for previously
        # released elements

        # following implementation results in an extra array called
        # fw_d_fref but easy to read
        v0 = substance.get_viscosity(self.water.get('temperature', 'K'))
        if v0 is not None:
            fw_d_fref = data['frac_water'][mask]/self.visc_f_ref
            data['viscosity'][mask] = \
                (v0 * np.exp(v0 * self.visc_curvfit_param *
                             data['frac_lost'][mask]) *
                 (1 + (fw_d_fref/(1.187 - fw_d_fref)))**2.49)

        # todo: Need formulas to update density
        # prev_rel = sc.num_released-new_LEs
        # if prev_rel > 0:
        #    update density, viscosity .. etc

        # update self.spreading.thickness_limit based on type of substance
        # create 'frac_coverage' array and pass it in to scale area by it
        # update_area will only update the area for particles with
        # thickness greater than some minimum thickness and the frac_coverage
        # is only applied to LEs whose area is updated. Elements below a min
        # thickness should not be updated
        data['area'][mask] = \
            self.spreading.update_area(self.water.get('kinematic_viscosity',
                                                      'square meter per second'),
                                       data['init_area'][mask],
                                       data['init_volume'][mask],
                                       data['relative_bouyancy'][mask],
                                       data['age'][mask],
                                       data['thickness'][mask],
                                       data['area'][mask],
                                       data['frac_coverage'][mask])

        # update thickness per the new area
        data['thickness'][mask] = data['init_volume'][mask]/data['area'][mask]

    def _set_relative_bouyancy(self, rho_oil):
        '''
        relative bouyancy of oil: (rho_water - rho_oil) / rho_water
        only 3 lines but made it a function for easy testing
        '''
        rho_h2o = self.water.get('density', 'kg/m^3')
        return (rho_h2o - rho_oil)/rho_h2o
