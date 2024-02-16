from string import Template

SET_TIMEZONE_SETTINGS = Template(
    "Your timezone is set to *$timezone*. \n"
    "Send me the name of your city or your location to set your "
    "timezone or touch in *Cancel* to go back."
)

TIMEZONE_SETTINGS_SET_SUCCESS = Template("Your timezone has been set to: *$timezone* ")

SET_REGISTRATION_TIMEZONE = Template("Welcome to Mitup Bot $first_name! Please, tell me your timezone.")

REGISTRATION_TIMEZONE_SET_SUCCESS = Template("Perfect! Your timezone is $timezone")

DEFAULT_MAIN_MENU_DESCRIPTION = "Welcome to Mitup Bot! \n" "Choose one of the following options:"

DEFAULT_SETTINGS_DESCRIPTION = "Configure MitUp."
