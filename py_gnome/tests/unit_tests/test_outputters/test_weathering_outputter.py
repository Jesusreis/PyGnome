'''
tests for geojson outputter
'''
import os
from glob import glob
from datetime import timedelta

import numpy as np
import pytest

from gnome.environment import constant_wind, Water
from gnome.weatherers import Evaporation, Dispersion, Skimmer, Burn
from gnome.spill import point_line_release_spill

from gnome.outputters import WeatheringOutput


@pytest.fixture(scope='module')
def model(sample_model, output_dir):
    model = sample_model['model']
    rel_start_pos = sample_model['release_start_pos']
    rel_end_pos = sample_model['release_end_pos']

    model.cache_enabled = True
    model.uncertain = False
    model.water = Water(311.15)

    print 'adding a Weatherer'
    model.environment += constant_wind(1.0, 0.0)

    N = 10  # a line of ten points
    line_pos = np.zeros((N, 3), dtype=np.float64)
    line_pos[:, 0] = np.linspace(rel_start_pos[0], rel_end_pos[0], N)
    line_pos[:, 1] = np.linspace(rel_start_pos[1], rel_end_pos[1], N)

    # print start_points
    model.duration = timedelta(hours=6)
    end_time = model.start_time + timedelta(hours=1)
    model.spills += point_line_release_spill(1000,
                                             start_position=rel_start_pos,
                                             release_time=model.start_time,
                                             end_release_time=end_time,
                                             end_position=rel_end_pos,
                                             substance='FUEL OIL NO.6',
                                             amount=1000,
                                             units='kg')

    # figure out mid-run save for weathering_data attribute, then add this in
    rel_time = model.spills[0].get('release_time')
    skim_start = rel_time + timedelta(hours=1)
    amount = model.spills[0].amount
    units = model.spills[0].units
    skimmer = Skimmer(.5*amount, units=units, efficiency=0.3,
                      active_start=skim_start,
                      active_stop=skim_start + timedelta(hours=1))
    model.weatherers += [Evaporation(model.water,
                                     model.environment[-1]),
                         Dispersion(),
                         Burn(),
                         skimmer]

    model.outputters += WeatheringOutput(output_dir=output_dir)
    model.rewind()

    return model


def test_init():
    'simple initialization passes'
    g = WeatheringOutput()
    assert g.output_dir is None


def test_model_webapi_output(model):
    'Test weathering outputter with a model since simplest to do that'
    model.rewind()

    # floating mass at beginning of step - though tests will only pass for
    # nominal values
    for step in model:
        assert 'WeatheringOutput' in step
        sum_mass = 0.0
        for key in step['WeatheringOutput']:
            if not isinstance(step['WeatheringOutput'][key], dict):
                continue

            for process in ('evaporated', 'burned', 'skimmed', 'dispersed'):
                assert (process in step['WeatheringOutput'][key])
                sum_mass += step['WeatheringOutput'][key][process]

            assert (step['WeatheringOutput'][key]['floating'] <=
                    step['WeatheringOutput'][key]['amount_released'])
            # For nominal, sum up all mass and ensure it equals the mass at
            # step initialization - ignore step 0
            sum_mass += step['WeatheringOutput'][key]['floating']
            np.isclose(sum_mass, step['WeatheringOutput'][key]['amount_released'])

        print 'Completed step: ', step

def test_model_dump_output(model):
    'Test weathering outputter with a model since simplest to do that'
    output_dir = model.outputters[0].output_dir
    model.rewind()
    model.full_run()
    files = glob(os.path.join(output_dir, '*.json'))
    assert len(files) == model.num_time_steps
    model.outputters[0].output_dir = None
