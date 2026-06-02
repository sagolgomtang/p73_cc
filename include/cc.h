#ifndef p73_cc_H
#define p73_cc_H

#include "p73_lib/robot_data.h"
#include "p73_lib/4bar_jac_func.h"
// #include "wholebody_functions.h"
#if __has_include(<onnxruntime/onnxruntime_cxx_api.h>)
#include <onnxruntime/onnxruntime_cxx_api.h>
#else
#include "onnxruntime_cxx_api.h"
#endif
#include "ament_index_cpp/get_package_share_directory.hpp"
#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <sensor_msgs/msg/joy.hpp>
#include <std_msgs/msg/empty.hpp>
#include <std_msgs/msg/float32_multi_array.hpp>
#include <std_msgs/msg/float64_multi_array.hpp>
#include <std_msgs/msg/string.hpp>
#include <chrono>
#include <fstream>
#include <sstream>
#include <array>
#include <thread>
#include <atomic>
#include <mutex>
#include <random>

using namespace Eigen;
using namespace std;

class CustomController
{
public:
    CustomController(DataContainer &dc, RobotEigenData &rd);

    void computeSlow();
    void computeFast();
    void copyRobotData(RobotEigenData &rd_l);

    DataContainer &dc_;
    RobotEigenData &rd_;
    RobotEigenData rd_cc_;

    ofstream writeFile;
    bool is_write_file_ = false;

    //////////////////////// Functions ////////////////////////
    void initVariable();
    void loadOnnX();
    void processNoise();
    void processObservation();
    void feedforwardPolicy();
    Vector3d quatRotateInverse(const Quaterniond &q, const Vector3d &v);

    //////////////////////// ONNX Runtime ////////////////////////
    size_t input_number, output_number;
    std::vector<std::string> input_names, output_names;
    std::vector<const char *> input_names_char, output_names_char;
    std::vector<Ort::Value> input_tensors, output_tensors;
    std::vector<std::vector<float>> input_states_buffer;

    int input_policy_idx_ = -1;
    int input_critic_idx_ = -1;
    int output_actions_idx_ = -1;
    int output_value_idx_ = -1;

    //////////////////////// Network Dimensions ////////////////////////
    // From rough_env_cfg.py PolicyCfg:
    //   base_ang_vel(3) + projected_gravity(3) + velocity_commands(3)
    //   + gait_phase_sin(1) + gait_phase_cos(1)
    //   + motor_joint_pos(12) + motor_joint_vel(12) + last_action(12) = 47
    static const int num_action = 12;         // lower body RL-controlled
    static const int num_single_obs = 47;

    int history_length_ = 10;     // overwritten from ONNX shape
    int policy_obs_dim_ = num_single_obs * 5;

    //////////////////////// Observation Buffers ////////////////////////
    std::vector<float> policy_frame_;                   // 47D single frame
    std::vector<float> policy_obs_hist_term_major_;     // 47*H term-major
    bool policy_hist_initialized_ = false;

    // Critic obs (if needed)
    std::vector<float> critic_obs_;

    //////////////////////// processNoise (TOCABI sim2real pattern) ////////////////////////
    // is_on_robot_: true=real robot (direct sensor), false=sim (noise + numerical diff)
    bool is_on_robot_ = false;

    // Joint state used by BOTH obs and PD (matching TOCABI)
    Matrix<double, MODEL_DOF, 1> q_noise_;       // joint position (noised in sim, direct on robot)
    Matrix<double, MODEL_DOF, 1> q_noise_pre_;   // previous step (for numerical diff)
    Matrix<double, MODEL_DOF, 1> q_vel_noise_;   // joint velocity (numerical diff in sim, direct on robot)

    double noise_time_cur_ = 0.0;
    double noise_time_pre_ = 0.0;
    bool noise_initialized_ = false;

    //////////////////////// Robot State ////////////////////////
    // Default joint positions. P73 JOINT_NAME order matches IsaacLab
    // _LOWER_JOINT_NAMES order (HipRoll, HipPitch, HipYaw, ...), so a single
    // 13D vector serves both lower-body obs (head<12>()) and full-body PD.
    Matrix<double, MODEL_DOF, 1> q_default_p73_;

    // RL action (IsaacLab order, 12D)
    Matrix<double, num_action, 1> rl_action_;
    Matrix<double, num_action, 1> last_action_processed_;  // raw * scale

    // Custom PD gains for RL policy (self-contained in cc.cpp, not from yaml).
    // These match the stiffness/damping used during IsaacLab training.
    VectorQd kp_p73_, kd_p73_;
    VectorQd torque_bound_p73_;
    Matrix<double, 12, 1> q_limit_lower_p73_, q_limit_upper_p73_;

    VectorQd torque_rl_;
    VectorQd torque_init_;
    VectorQd q_init_;
    VectorQd q_init_hold_;  // DEBUG: captured pose at mode entry
    VectorQd q_spline_;
    VectorQd torque_spline_;

    // 4-bar kinematics used in sim to reproduce motor-level torque clamping
    // through J^T (state_estimator does not populate rd_.four_bar_Jaco_ in simMode).
    FourBarKinematics sim_four_bar_;

    double action_scale_ = 0.5;  // from ActionsCfg scale

    //////////////////////// Timing ////////////////////////
    float start_time_;
    float time_inference_pre_ = 0.0;
    bool cc_init_ = true;

    // Policy rate: 50Hz (decimation=4, dt=0.005 → policy_dt=0.02)
    double policy_dt_ = 0.02;

    // Gait phase counter (50Hz step counter)
    int gait_step_counter_ = 0;
    int gait_period_steps_ = 70;  // from rough_env_cfg __post_init__

    // Velocity command (updated by ROS2 subscriber)
    std::mutex vel_mutex_;
    double target_vel_x_ = 0.0;
    double target_vel_y_ = 0.0;
    double target_vel_yaw_ = 0.0;
    double cmd_zero_max_ = 1.0e-3;

    // Direct joystick command config, copied from IsaacLab play_with_joy.py.
    int joy_axis_vx_ = 1;
    int joy_axis_vy_ = 0;
    int joy_axis_wz_ = 3;
    int joy_axis_cam_elev_ = 7;
    bool joy_invert_vx_ = false;
    bool joy_invert_vy_ = false;
    bool joy_invert_wz_ = false;
    double joy_max_vx_ = 1.0;
    double joy_max_vy_ = 0.5;
    double joy_max_wz_ = 0.6;
    double joy_deadzone_ = 0.20;
    double joy_timeout_s_ = 0.5;
    int joy_btn_lock_ = 0;
    int joy_btn_cam_yaw_pos_ = 4;
    int joy_btn_cam_yaw_neg_ = 5;
    int joy_btn_cam_zoom_in_ = 6;
    int joy_btn_cam_zoom_out_ = 7;
    int joy_btn_reset_ = 9;
    int joy_btn_exit_ = 10;
    double joy_cam_yaw_step_ = 0.025;
    double joy_cam_pitch_rate_ = 0.012;
    double joy_cam_pitch_max_ = 1.2;
    double joy_cam_zoom_in_ = 0.985;
    double joy_cam_zoom_out_ = 1.015;
    double joy_cam_radius_min_ = 0.5;
    double joy_cam_radius_max_ = 20.0;
    double joy_cam_pitch_ = 0.0;
    double joy_cam_radius_ = 1.0;
    bool joy_cmd_locked_ = false;
    double joy_locked_vx_ = 0.0;
    double joy_locked_vy_ = 0.0;
    double joy_locked_wz_ = 0.0;
    bool joy_command_active_ = false;
    std::array<int, 16> joy_prev_buttons_{};
    std::chrono::steady_clock::time_point joy_last_time_{};
    bool direct_joystick_enabled_ = true;
    std::string direct_joystick_device_ = "/dev/input/js0";
    int direct_joystick_fd_ = -1;
    double direct_joystick_last_open_try_s_ = -1.0;
    std::vector<float> direct_joy_axes_;
    std::vector<int32_t> direct_joy_buttons_;
    std::vector<int32_t> direct_joy_press_latch_;
    std::thread direct_joy_thread_;
    std::atomic<bool> direct_joy_running_{false};
    int prev_axis6_dir_ = 0;

    double value_ = 0.0;

    string weight_dir_;

    //////////////////////// ROS2 Velocity Command Subscriber ////////////////////////
    // Uses dc_.node_ (main controller node) to share its DDS participant,
    // avoiding communication issues when running with sudo on real robot.
    rclcpp::CallbackGroup::SharedPtr vel_cbg_;
    rclcpp::executors::SingleThreadedExecutor vel_executor_;
    rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr vel_sub_;
    rclcpp::Subscription<sensor_msgs::msg::Joy>::SharedPtr joy_sub_;
    rclcpp::Publisher<std_msgs::msg::Float32MultiArray>::SharedPtr tocabi_cam_cmd_pub_;
    rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr joy_camera_pub_;
    rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr joy_reset_pub_;
    rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr joy_exit_pub_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr gui_cmd_pub_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr sim_command_pub_;
    std::thread vel_spin_thread_;
    std::atomic<bool> vel_spin_running_{false};

    void velCmdCallback(const geometry_msgs::msg::Twist::SharedPtr msg);
    void joyCmdCallback(const sensor_msgs::msg::Joy::SharedPtr msg);
    void pollDirectJoystick();
    void closeDirectJoystick();
    void startVelSubscriber();
    void stopVelSubscriber();

private:
    Ort::Env env;
    Ort::Session session;
    Ort::MemoryInfo memory_info;
};

#endif
