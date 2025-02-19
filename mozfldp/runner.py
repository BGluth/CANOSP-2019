import json

from mozfldp import random_data_gen
from mozfldp.simulation_util import server_update
from mozfldp.fed_avg_w_dp import run_fed_avg_with_dp, FedAvgWithDpParams

import pandas as pd
import sklearn
from sklearn.model_selection import ParameterGrid, train_test_split
import numpy as np


def run_data_gen_blob(s_prms):
    return _run_gen_func(s_prms, random_data_gen.generate_blob_data)


def run_data_gen_rand(s_prms):
    return _run_gen_func(s_prms, random_data_gen.generate_random_data)


# Blob and rand are called identically, so it makes sense to wrap this in a func
def _run_gen_func(s_prms, gen_func):
    g_prms = create_g_params_from_s_params(s_prms)
    rand_data = gen_func(g_prms)
    return random_data_gen.transform_data_for_simulator_format(rand_data, g_prms)


def read_data_from_file(s_prms):
    file_path = s_prms[Runner.P_KEY_DATA_FILE_PATH]
    df = pd.read_csv(file_path)

    g_prms = create_g_params_from_s_params(s_prms)
    return random_data_gen.transform_data_for_simulator_format(df, g_prms)


def create_g_params_from_s_params(s_prms):
    return random_data_gen.InputGenParams(
        s_prms[Runner.P_KEY_NUM_SAMPLES],
        s_prms[Runner.P_KEY_NUM_LABELS],
        s_prms[Runner.P_KEY_NUM_FEATURES],
        s_prms[Runner.P_KEY_NUM_USERS],
    )


def run_fed_learn_sim(s_prms, data):
    num_labels = s_prms[Runner.P_KEY_NUM_LABELS]
    num_features = s_prms[Runner.P_KEY_NUM_FEATURES]
    num_rounds = s_prms[Runner.P_KEY_NUM_ROUNDS]
    batch_size = s_prms[Runner.P_KEY_BATCH_SIZE]
    num_epochs = s_prms[Runner.P_KEY_NUM_EPOCHS]
    rand_seed = s_prms[Runner.P_KEY_RAND_SEED]

    # Note: data is already transformed for sim format

    sim_labels, sim_features = data
    features = np.array(sim_features)
    labels = np.array(sim_labels)

    # Split the data into training and testing sets
    X_train, X_test, y_train, y_test = train_test_split(
        features, labels, test_size=0.4, random_state=rand_seed
    )

    init_weights = np.zeros((num_labels, num_features), dtype=np.float64, order="C")
    init_intercept = np.zeros(num_labels, dtype=np.float64, order="C")

    # Find all the permutations of the parameters
    param_grid = {
        "client_fraction": [1, 0.1],
        "epoch": [1, num_epochs],
        "batch_size": [batch_size],  # TODO: need to implement an infinite batch size
        "init_weight": [[init_weights, init_intercept]],
        "num_rounds": [num_rounds],
    }

    # run training/testing over all parameter combinations to get the best combination
    for params in ParameterGrid(param_grid):
        print("Training...")
        print("Params: ", params)
        classifier = server_update(
            params["init_weight"],
            params["client_fraction"],
            params["num_rounds"],
            X_train,
            y_train,
            params["epoch"],
            params["batch_size"],
            False,
            rand_seed,
        )
        weights = [classifier.coef_, classifier.intercept_]

        # need to remove the client dimension from our data for testing
        # ex: [[[1, 1], [2, 2]], [[3, 3], [4, 4]]] needs to become [[1, 1], [2, 2], [3, 3], [4, 4]] for features
        # and [[1, 2], [3, 4]] needs to become [1, 2, 3, 4] for labels
        reshaped_X_test = np.reshape(
            X_test, (X_test.shape[0] * X_test.shape[1], X_test.shape[2])
        )
        reshaped_y_test = np.reshape(y_test, y_test.size)

        score = classifier.score(reshaped_X_test, reshaped_y_test)

        print("Weights: {}\nScore: {:f}\n\n".format(weights, score))

        return score


def fed_avg_with_dp(s_prms, data):
    prms = FedAvgWithDpParams(
        s_prms[Runner.P_KEY_NUM_USERS],
        s_prms[Runner.P_KEY_NUM_FEATURES],
        s_prms[Runner.P_KEY_NUM_LABELS],
        s_prms[Runner.P_KEY_NUM_ROUNDS],
        s_prms[Runner.P_KEY_BATCH_SIZE],
        s_prms[Runner.P_KEY_NUM_EPOCHS],
        s_prms[Runner.P_KEY_WEIGHT_MOD],
        s_prms[Runner.P_KEY_USER_SEL_PROB],
        s_prms[Runner.P_KEY_SENSITIVITY],
        s_prms[Runner.P_KEY_NOISE_SCALE],
        s_prms[Runner.P_KEY_RAND_SEED],
    )

    labels, features = data
    labels = np.array(labels)
    features = np.array(features)

    # Split the data into training and testing sets
    feat_train, feat_test, label_train, label_test = train_test_split(
        features, labels, test_size=0.4, random_state=prms.rand_seed
    )

    # Since we are breaking the data into testing/training sets, we
    # need to update the num_users parameter for the sim (since 100
    # users --> 60)
    prms.num_users = len(feat_train)

    training_data = [label_train, feat_train]
    [trained_coef, trained_inter] = run_fed_avg_with_dp(prms, training_data)

    clf = sklearn.linear_model.SGDClassifier(
        loss="hinge", penalty="l2", random_state=prms.rand_seed
    )
    clf.coef_ = trained_coef
    clf.intercept_ = trained_inter
    clf.classes_ = np.unique(labels)

    # Remove client dimension from arrays...
    reshaped_feat_test = np.reshape(
        feat_test, (feat_test.shape[0] * feat_test.shape[1], feat_test.shape[2])
    )

    reshaped_label_test = np.reshape(label_test, label_test.size)

    score = clf.score(reshaped_feat_test, reshaped_label_test)
    print("Score: {}".format(score))

    return score


class RunnerException(Exception):
    def __init__(self, msg):
        super(RunnerException, self).__init__(msg)


class Runner:
    SIM_TYPE_FED_LEARNING = "fed_learning"
    SIM_TYPE_FED_AVG_WITH_DP = "fed_avg_with_dp"

    DATA_GEN_TYPE_DATA_FROM_FILE = "file_data"
    DATA_GEN_TYPE_BLOB = "data_gen_blob"
    DATA_GEN_TYPE_RAND = "data_gen_rand"

    P_KEY_DATA_FILE_PATH = "data_file_path"

    P_KEY_NUM_SAMPLES = "num_samples"
    P_KEY_NUM_LABELS = "num_labels"
    P_KEY_NUM_FEATURES = "num_features"
    P_KEY_NUM_USERS = "num_users"

    P_KEY_NUM_ROUNDS = "num_rounds"
    P_KEY_BATCH_SIZE = "batch_size"
    P_KEY_NUM_EPOCHS = "num_epochs"
    P_KEY_RAND_SEED = "rand_seed"

    P_KEY_WEIGHT_MOD = "weight_mod"
    P_KEY_USER_SEL_PROB = "user_sel_prob"
    P_KEY_SENSITIVITY = "sensitivity"
    P_KEY_NOISE_SCALE = "noise_scale"

    _sim_run_func_ltable = {
        SIM_TYPE_FED_LEARNING: (
            run_fed_learn_sim,
            {
                P_KEY_NUM_ROUNDS,
                P_KEY_BATCH_SIZE,
                P_KEY_NUM_EPOCHS,
                P_KEY_NUM_SAMPLES,
                P_KEY_NUM_FEATURES,
                P_KEY_NUM_USERS,
                P_KEY_RAND_SEED,
            },
        ),
        SIM_TYPE_FED_AVG_WITH_DP: (
            fed_avg_with_dp,
            {
                P_KEY_NUM_USERS,
                P_KEY_NUM_LABELS,
                P_KEY_NUM_FEATURES,
                P_KEY_NUM_ROUNDS,
                P_KEY_BATCH_SIZE,
                P_KEY_NUM_EPOCHS,
                P_KEY_WEIGHT_MOD,
                P_KEY_USER_SEL_PROB,
                P_KEY_SENSITIVITY,
                P_KEY_NOISE_SCALE,
            },
        ),
    }

    _data_gen_run_func_ltable = {
        DATA_GEN_TYPE_DATA_FROM_FILE: (
            read_data_from_file,
            {
                P_KEY_NUM_SAMPLES,
                P_KEY_NUM_LABELS,
                P_KEY_NUM_FEATURES,
                P_KEY_NUM_USERS,
                P_KEY_DATA_FILE_PATH,
            },
        ),
        DATA_GEN_TYPE_BLOB: (
            run_data_gen_blob,
            {P_KEY_NUM_SAMPLES, P_KEY_NUM_LABELS, P_KEY_NUM_FEATURES, P_KEY_NUM_USERS},
        ),
        DATA_GEN_TYPE_RAND: (
            run_data_gen_rand,
            {P_KEY_NUM_SAMPLES, P_KEY_NUM_LABELS, P_KEY_NUM_FEATURES, P_KEY_NUM_USERS},
        ),
    }

    def __init__(self, param_json, s_type, d_type):
        self._params = json.loads(param_json)

        s_run_func, s_prq_prms = Runner._sim_run_func_ltable[s_type]
        g_run_func, g_prq_prms = Runner._data_gen_run_func_ltable[d_type]

        self._verify_sim_has_required_params_for_given_run_func(s_prq_prms, s_type)
        self._verify_sim_has_required_params_for_given_run_func(g_prq_prms, d_type)

        print('Generating "{}" data...'.format(d_type))
        generated_data = g_run_func(self._params)

        print('Running the "{}" simulation...'.format(s_type))
        s_run_func(self._params, generated_data)

        print("Finished!")

    def _verify_sim_has_required_params_for_given_run_func(
        self, run_func_params, run_func_key
    ):
        missing_req_params = run_func_params.difference(self._params)

        if len(missing_req_params) == 0:
            return

        ex_msg = "Can not run {} because the following required parameters are missing: ".format(
            run_func_key
        )

        for missing_p in missing_req_params:
            ex_msg += "\n- {}".format(missing_p)

        raise RunnerException(ex_msg)
